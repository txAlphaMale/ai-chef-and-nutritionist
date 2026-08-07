"""The cooking-timer sound library (GET/POST/DELETE), and the audio
itself.

Kept as its own router rather than folded into /api/system/settings
because a sound is a FILE with a lifecycle -- uploaded, previewed,
deleted, streamed -- and settings are scalar values. Same reasoning that
put knowledge files on their own router.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SoundFile
from app.schemas.sound import SoundRead
from app.services import sound_service

router = APIRouter(prefix="/api/sounds", tags=["sounds"])


_DEFAULT_FOR_BY_SLUG = {slug: role for slug, _n, _b, role in sound_service.BUILTIN_SOUNDS if role}


def _to_read(sound: SoundFile) -> SoundRead:
    return SoundRead(
        id=sound.id,
        name=sound.name,
        slug=sound.slug,
        is_builtin=sound.is_builtin,
        default_for=_DEFAULT_FOR_BY_SLUG.get(sound.slug or ""),
        missing_file=not os.path.exists(sound.storage_path),
    )


@router.get("", response_model=list[SoundRead])
def list_sounds(db: Session = Depends(get_db)):
    """Built-ins first, then uploads by name -- so the dropdown's default
    ordering is stable and the shipped tones are always reachable without
    scrolling past a long personal library."""
    sounds = db.query(SoundFile).order_by(SoundFile.is_builtin.desc(), SoundFile.name).all()
    return [_to_read(s) for s in sounds]


@router.post("", response_model=SoundRead, status_code=201)
async def upload_sound(name: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    try:
        path = sound_service.save_upload(file.filename or "", file.content_type, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    label = name.strip() or os.path.splitext(file.filename or "Sound")[0]
    sound = SoundFile(name=label[:120], storage_path=path, slug=None, is_builtin=False)
    db.add(sound)
    db.commit()
    db.refresh(sound)
    return _to_read(sound)


@router.delete("/{sound_id}", status_code=204)
def delete_sound(sound_id: int, db: Session = Depends(get_db)):
    sound = db.get(SoundFile, sound_id)
    if sound is None:
        raise HTTPException(status_code=404, detail="Sound not found")
    if sound.is_builtin:
        # Not squeamishness: every timer's warning and finish sound is
        # chosen from this list, and a list that can be emptied is a
        # timer that goes off in silence.
        raise HTTPException(status_code=400, detail="Built-in sounds can't be deleted")
    sound_service.delete_file(sound.storage_path)
    db.delete(sound)
    db.commit()


@router.get("/{sound_id}/audio")
def get_sound_audio(sound_id: int, db: Session = Depends(get_db)):
    sound = db.get(SoundFile, sound_id)
    if sound is None or not os.path.exists(sound.storage_path):
        raise HTTPException(status_code=404, detail="No audio for that sound")
    return FileResponse(sound.storage_path, media_type=sound_service.guess_content_type(sound.storage_path))
