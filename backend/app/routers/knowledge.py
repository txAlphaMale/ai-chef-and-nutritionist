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

from app.database import SessionLocal, get_db
from app.models import KnowledgeFile
from app.schemas.jobs import JobEnqueuedResponse
from app.schemas.knowledge import KnowledgeFileRead, KnowledgeFileUpdate
from app.services import job_queue, knowledge_service, settings_service

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
        chunk_count=len(kf.chunks),
        created_at=kf.created_at,
    )


@router.get("", response_model=list[KnowledgeFileRead])
def list_knowledge_files(db: Session = Depends(get_db)):
    files = db.query(KnowledgeFile).order_by(KnowledgeFile.filename).all()
    return [_to_read(f) for f in files]


@router.post("", response_model=KnowledgeFileRead, status_code=201)
async def upload_knowledge_file(file: UploadFile, description: str | None = Form(None), db: Session = Depends(get_db)):
    """Backlog B11.1 (2026-08-01): the embedding-indexing step
    (knowledge_service.ensure_indexed -- one blocking Ollama embed() call
    PER CHUNK, potentially dozens to hundreds in a row for a large file)
    now runs as a background job instead of inline. This endpoint was
    `async def` calling that blocking loop directly with no thread
    offload -- likely the single worst offender behind the reported
    "whole app freezes" bug, since a big file meant MANY sequential
    blocking calls in one request, not just one.

    The file row is still created, extracted, and returned immediately
    with chunk_count=0 -- unlike every other B11.1 conversion, the
    caller doesn't need a job_id to get a useful response here, since
    the file already exists and is usable (just not yet indexed for
    retrieval). The persistent job badge still shows indexing in
    progress for anyone watching; a later GET on this list reflects the
    real chunk_count once the job finishes.

    Also a genuine improvement over the previous behavior, not just a
    refactor: the old code wrapped ensure_indexed in a bare
    `except: pass`, so a failed indexing pass (e.g. Ollama unreachable)
    left chunk_count at 0 forever with NO visible explanation anywhere.
    A failed indexing job now shows up in the job registry with a real
    error message instead."""
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
    kf_id = kf.id
    kf_filename = kf.filename

    def _run() -> dict:
        job_db = SessionLocal()
        try:
            job_kf = job_db.get(KnowledgeFile, kf_id)
            if job_kf is None:
                raise RuntimeError("Knowledge file was deleted before indexing could run")
            knowledge_service.ensure_indexed(job_db, knowledge_file=job_kf)
            job_db.refresh(job_kf)
            return _to_read(job_kf).model_dump(mode="json")
        finally:
            job_db.close()

    job_queue.enqueue("knowledge_reindex", f"Indexing: {kf_filename}", _run)
    return _to_read(kf)


# Declared before the /{file_id} routes below -- FastAPI matches path
# operations in declaration order, so a static path after a dynamic one
# gets swallowed by it. Same discipline as routers/inventory.py.


@router.get("/index-status")
def knowledge_index_status(db: Session = Depends(get_db)):
    """Whether retrieval is working from a current index.

    Audit P2-6. `search_knowledge` used to silently reindex anything
    stale before every retrieval -- which meant an embed-model change, or
    a file uploaded while Ollama was down, turned the NEXT chat message
    into a full re-embed on the single job worker thread, blocking every
    other AI feature behind it. Retrieval no longer does that, so the
    staleness has to be visible instead: this is what makes it visible,
    and /reindex below is how it gets fixed, deliberately."""
    stale = knowledge_service.find_stale_files(db)
    return {
        "embed_model": settings_service.get_setting(db, "ollama_embed_model"),
        "stale_count": len(stale),
        "stale_filenames": [kf.filename for kf in stale],
    }


@router.post("/reindex", response_model=JobEnqueuedResponse, status_code=202)
def reindex_knowledge(db: Session = Depends(get_db)):
    """Re-chunks and re-embeds every active file whose index doesn't
    match the configured embed model. Queued, never inline: this is N
    sequential Ollama calls per file and belongs nowhere near a request
    handler."""
    stale_count = len(knowledge_service.find_stale_files(db))

    def _run() -> dict:
        job_db = SessionLocal()
        try:
            failed = knowledge_service.ensure_indexed(job_db)
            return {"failed_files": failed, "failed_count": len(failed)}
        finally:
            job_db.close()

    job_id, created = job_queue.enqueue(
        "knowledge_reindex",
        f"Reindexing {stale_count} knowledge file(s)",
        _run,
        # One reindex at a time -- the work is idempotent, and queueing a
        # second while the first is running would just re-embed the same
        # files on the same single worker thread.
        dedup_key="knowledge_reindex",
    )
    return JobEnqueuedResponse(job_id=job_id, created=created)


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
