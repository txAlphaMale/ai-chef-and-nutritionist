"""Nutritionist knowledge file management (upload/list/update/delete).
Uploaded PDFs/text/markdown have their text extracted once at upload
time (knowledge_service.extract_text) and stored so meal-plan generation
(health_service.build_knowledge_context, wired into meal_plan_service)
can ground on them without re-parsing the file on every request. The
full extracted text is never returned by these endpoints -- only a short
excerpt for a sanity-check preview -- since it can be large and isn't
meant for display.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import KnowledgeFile
from app.schemas.knowledge import KnowledgeFileRead, KnowledgeFileUpdate
from app.services import knowledge_service

router = APIRouter(prefix="/api/knowledge-files", tags=["knowledge-files"])

EXCERPT_LENGTH = 300


def _to_read(kf: KnowledgeFile) -> KnowledgeFileRead:
    return KnowledgeFileRead(
        id=kf.id,
        filename=kf.filename,
        content_type=kf.content_type,
        description=kf.description,
        is_active=kf.is_active,
        has_content=bool(kf.content),
        content_excerpt=(kf.content[:EXCERPT_LENGTH] if kf.content else None),
        created_at=kf.created_at,
    )


@router.get("", response_model=list[KnowledgeFileRead])
def list_knowledge_files(db: Session = Depends(get_db)):
    files = db.query(KnowledgeFile).order_by(KnowledgeFile.filename).all()
    return [_to_read(f) for f in files]


@router.post("", response_model=KnowledgeFileRead, status_code=201)
async def upload_knowledge_file(
    file: UploadFile, description: str | None = Form(None), db: Session = Depends(get_db)
):
    raw_bytes = await file.read()
    storage_path = knowledge_service.save_file(file.filename, raw_bytes)
    content = knowledge_service.extract_text(file.filename, file.content_type, raw_bytes)

    kf = KnowledgeFile(
        filename=file.filename,
        storage_path=storage_path,
        content_type=file.content_type,
        description=description,
        content=content,
        is_active=True,
    )
    db.add(kf)
    db.commit()
    db.refresh(kf)
    return _to_read(kf)


@router.patch("/{file_id}", response_model=KnowledgeFileRead)
def update_knowledge_file(file_id: int, payload: KnowledgeFileUpdate, db: Session = Depends(get_db)):
    kf = db.get(KnowledgeFile, file_id)
    if kf is None:
        raise HTTPException(status_code=404, detail="Knowledge file not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(kf, field, value)
    db.commit()
    db.refresh(kf)
    return _to_read(kf)


@router.delete("/{file_id}", status_code=204)
def delete_knowledge_file(file_id: int, db: Session = Depends(get_db)):
    kf = db.get(KnowledgeFile, file_id)
    if kf is None:
        raise HTTPException(status_code=404, detail="Knowledge file not found")
    knowledge_service.delete_file(kf.storage_path)
    db.delete(kf)
    db.commit()
    return None
