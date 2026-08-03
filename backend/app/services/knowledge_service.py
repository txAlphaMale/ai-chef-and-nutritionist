"""Storage, text extraction, and retrieval-based grounding for imported
nutritionist knowledge files (PDF/txt/md) -- lets the household ground
the AI with reference material (e.g. a specific diet plan, a doctor's
guidance sheet) that meal-plan generation and persistent chat can draw
on. See health_service.build_knowledge_context() for how retrieval
results get formatted into a prompt.

Files are stored on disk (same Docker volume as the SQLite DB, same
pattern as secrets_crypto.py's key file) under a UUID-based name to
avoid filename collisions/traversal from user-supplied names; the
original filename is kept in the DB row for display.

--- RAG design (added 2026-07-31, replacing the original Phase 6
"concatenate every active file, truncate to a combined budget" approach
-- see PROJECT-PLAN.md's former "Knowledge/RAG" backlog entry for the
full writeup this implements) ---

Ported from the sibling Fiduciary project's `portfolio-api/knowledge.py`
+ `store.py` at the author's request, adapted to Chef's existing
per-file-row upload model rather than Fiduciary's watched-folder one:

- Chunking (`chunk_text`) and cosine similarity (`_cosine_similarity`)
  are ported essentially verbatim -- fixed character budget with
  paragraph/sentence-boundary-aware breaks, no vector DB needed at this
  corpus size.
- Retrieval (`search_knowledge`) replaces "inject the whole corpus into
  every prompt" with "embed the query, rank all chunks by cosine
  similarity, return the top k" -- the single biggest gap the backlog
  note called out, and what actually lets a knowledge base scale past a
  handful of short files.
- Incremental reindexing is simpler than Fiduciary's: Chef's
  KnowledgeFile rows are explicit, user-managed uploads (not files
  discovered by scanning a watched folder), and `content` is set once
  at upload and never edited afterward -- so there's no "did the bytes
  change" case to detect, only "has this file been indexed at all" and
  "did the configured embedding model change since it was." Both are
  covered by comparing `KnowledgeFile.indexed_embed_model` against the
  current `ollama_embed_model` setting (`ensure_indexed`), without
  needing Fiduciary's content-hash-keyed cross-file dedup tables --
  Chef's files already have a stable identity (the DB row) that
  survives a rename-equivalent (there isn't one; editing a filename
  isn't even exposed), so true byte-level dedup wasn't worth porting.
- Deliberately NOT ported: Fiduciary's `search_knowledge` as a
  model-invoked tool call. Whether Chef's target Ollama models support
  reliable tool-calling is unverified, and nothing else in this codebase
  uses native tool-calling (chat's action-proposal system and every
  other AI-structured-output feature here use prompt-engineered JSON
  instructions instead) -- so retrieval results are injected directly
  into the prompt as reference material, the simpler fallback Fiduciary's
  own backlog note allowed for. Also not ported: background-thread
  reindexing with a pollable progress endpoint -- indexing runs
  synchronously and inline (on knowledge-file upload, and lazily before
  any retrieval call finds stale/missing chunks), which is fine given
  Chef's realistically small corpus; worth revisiting only if a real
  deployment's knowledge base and embed latency make that noticeably
  slow.
"""
from __future__ import annotations

import contextlib
import math
import os
import re
import uuid

from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeFile
from app.services import ollama_client, settings_service
from app.services.recipe_service import extract_pdf_text

KNOWLEDGE_FILES_DIR = os.environ.get("KNOWLEDGE_FILES_DIR", "/app/data/knowledge")

TEXT_EXTENSIONS = (".txt", ".md", ".markdown")

# Same defaults as Fiduciary's knowledge.py (1400 chars, 200 overlap) --
# a reasonable chunk size for embedding models in this class, and no
# reason to diverge without evidence it matters for Chef's corpus.
CHUNK_CHARS = int(os.environ.get("KNOWLEDGE_CHUNK_CHARS", "1400"))
CHUNK_OVERLAP = int(os.environ.get("KNOWLEDGE_CHUNK_OVERLAP", "200"))


def save_file(filename: str, raw_bytes: bytes) -> str:
    """Writes raw_bytes to a new UUID-named file under KNOWLEDGE_FILES_DIR
    and returns the full storage path. Atomic write (temp file + rename)
    so a crash mid-write can't leave a corrupt file behind."""
    os.makedirs(KNOWLEDGE_FILES_DIR, exist_ok=True)
    ext = os.path.splitext(filename)[1][:20]  # keep the extension, bound its length
    storage_path = os.path.join(KNOWLEDGE_FILES_DIR, f"{uuid.uuid4().hex}{ext}")
    tmp_path = storage_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(raw_bytes)
    os.replace(tmp_path, storage_path)
    return storage_path


def extract_text(filename: str, content_type: str | None, raw_bytes: bytes) -> str | None:
    """Best-effort plain-text extraction for grounding purposes. Returns
    None (not an error) for a type we can't extract from -- the file is
    still stored and viewable/downloadable, it just won't ground the AI."""
    lower_name = filename.lower()
    if (content_type == "application/pdf") or lower_name.endswith(".pdf"):
        try:
            return extract_pdf_text(raw_bytes) or None
        except Exception:
            return None

    if lower_name.endswith(TEXT_EXTENSIONS) or (content_type or "").startswith("text/"):
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None

    return None


def delete_file(storage_path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(storage_path)


# --- Chunking & similarity (pure functions, ported from Fiduciary's
# knowledge.py `_chunk`/`_cos`) ------------------------------------------


def chunk_text(text: str) -> list[str]:
    """Splits `text` into ~CHUNK_CHARS-sized pieces with CHUNK_OVERLAP
    characters of overlap between consecutive chunks, trying to break on
    a paragraph or (failing that) sentence boundary near the target end
    rather than cutting mid-sentence. Collapses runs of whitespace first
    so extracted-PDF text (often full of irregular spacing) chunks
    sensibly. Returns [] for empty/whitespace-only input."""
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    i, n = 0, len(text)
    while i < n:
        end = min(n, i + CHUNK_CHARS)
        if end < n:
            boundary = text.rfind("\n\n", i, end)
            if boundary < 0:
                boundary = text.rfind(". ", i, end)
            if boundary > i + CHUNK_CHARS // 2:
                end = boundary + 1
        segment = text[i:end].strip()
        if segment:
            chunks.append(segment)
        i = max(end - CHUNK_OVERLAP, end) if end >= n else end - CHUNK_OVERLAP
    return chunks


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return -1.0
    # strict=True: two embedding vectors of different lengths would mean
    # the corpus was indexed under a different model than the query was
    # embedded with, which should fail loudly rather than silently score
    # on whichever prefix happened to line up.
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return (dot / (norm_a * norm_b)) if norm_a and norm_b else -1.0


# --- Indexing & retrieval --------------------------------------------------


def ensure_indexed(db: Session, knowledge_file: KnowledgeFile | None = None) -> list[str]:
    """(Re)chunks and embeds every active KnowledgeFile whose
    indexed_embed_model doesn't match the currently-configured
    ollama_embed_model setting -- covers both "never indexed" (null)
    and "the embed model changed since this was indexed" in one check.
    Pass a specific `knowledge_file` to index just that one (e.g. right
    after upload) instead of scanning all of them. Returns a list of
    filenames that failed to embed (embedding errors are per-file,
    non-fatal -- a household with an unreachable Ollama still gets to
    use the rest of the app; that file's chunks are just left in
    whatever state they were in, and retried on the next call)."""
    embed_model = settings_service.get_setting(db, "ollama_embed_model")
    if knowledge_file is not None:
        candidates = [knowledge_file]
    else:
        candidates = db.query(KnowledgeFile).filter_by(is_active=True).all()

    failed: list[str] = []
    for kf in candidates:
        if not kf.is_active or not kf.content:
            continue
        if kf.indexed_embed_model == embed_model and kf.chunks:
            continue  # already indexed under the current model
        chunks = chunk_text(kf.content)
        if not chunks:
            continue
        chunk_rows = []
        had_error = False
        for i, chunk in enumerate(chunks):
            try:
                vector = ollama_client.embed(db, chunk, model=embed_model)
            except Exception:
                had_error = True
                break
            if vector:
                chunk_rows.append(KnowledgeChunk(chunk_index=i, text=chunk, vector=vector))
        if had_error or not chunk_rows:
            failed.append(kf.filename)
            continue
        kf.chunks.clear()
        kf.chunks.extend(chunk_rows)
        kf.indexed_embed_model = embed_model
        db.commit()
    return failed


def find_stale_files(db: Session) -> list[KnowledgeFile]:
    """Active knowledge files whose index doesn't match the currently
    configured embed model -- i.e. what a reindex would actually work on.
    Used to report staleness without triggering the work."""
    embed_model = settings_service.get_setting(db, "ollama_embed_model")
    return [
        kf
        for kf in db.query(KnowledgeFile).filter_by(is_active=True).all()
        if kf.content and (kf.indexed_embed_model != embed_model or not kf.chunks)
    ]


def search_knowledge(db: Session, query: str, k: int = 4) -> list[dict]:
    """Embeds `query` and returns the top-k most similar chunks across
    every active, indexed knowledge file, each as {"source" (filename),
    "score", "text"}. Returns [] (not an error) if the query is empty,
    nothing is indexed yet, or the embed call itself fails -- callers
    treat an empty result as "no relevant reference material," same as
    before there was any knowledge base at all.

    Audit P2-6: this used to call `ensure_indexed(db)` first, which made
    retrieval a potentially unbounded operation. Retrieval runs inside
    chat and meal-plan generation, both of which run on the single job
    worker thread -- so a stale index (any embed-model change, or a file
    uploaded while Ollama was down) turned the next chat message into N
    sequential embed calls on that one thread, wedging every other AI
    feature in the app behind it. That is the same single-point-of-
    failure shape as audit P0-2, reached by a different route.

    Retrieval now uses whatever index exists and never builds one.
    Reindexing is an explicit queued job (`POST /api/knowledge/reindex`),
    and `find_stale_files` above lets the UI say so rather than silently
    fixing it at the worst possible moment."""
    query = (query or "").strip()
    if not query:
        return []
    embed_model = settings_service.get_setting(db, "ollama_embed_model")
    rows = (
        db.query(KnowledgeChunk)
        .join(KnowledgeFile)
        .filter(KnowledgeFile.is_active.is_(True))
        .all()
    )
    if not rows:
        return []
    try:
        query_vector = ollama_client.embed(db, query, model=embed_model)
    except Exception:
        return []
    if not query_vector:
        return []
    scored = sorted(
        ((round(_cosine_similarity(query_vector, row.vector), 4), row) for row in rows),
        key=lambda pair: -pair[0],
    )
    return [
        {"source": row.knowledge_file.filename, "score": score, "text": row.text}
        for score, row in scored[: max(1, k)]
    ]
