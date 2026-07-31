"""Persistent chat: history storage per session, and message send/reply.

The chat is deliberately "dumb" server-side about actions -- it proposes
them (see chat_service.py for the schema) but never executes them. The
frontend renders each proposed action as a confirmable card; clicking
confirm calls one of these EXISTING endpoints directly, matching the
action's `type`:
- inventory_deduct  -> POST /api/inventory/deduct
- inventory_update  -> POST /api/inventory/update-by-name
- inventory_add     -> POST /api/inventory (existing create endpoint)
- meal_plan_confirm_entry -> POST /api/meal-plans/{meal_plan_id}/entries/{entry_id}/confirm
- meal_plan_skip_entry    -> POST /api/meal-plans/{meal_plan_id}/entries/{entry_id}/skip
This keeps one source of truth for what an action actually does, rather
than a parallel action-execution layer duplicating those endpoints.

Route ordering matters -- the static /sessions path is declared before
the dynamic /sessions/{session_id} route.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChatMessage
from app.schemas.chat import ChatMessageRead, ChatSendRequest, ChatSendResponse, ChatSessionSummary
from app.services import chat_service, ollama_client

router = APIRouter(prefix="/api/chat", tags=["chat"])

PREVIEW_LENGTH = 80


@router.get("/sessions", response_model=list[ChatSessionSummary])
def list_sessions(db: Session = Depends(get_db)):
    """Sessions are implicit -- derived from distinct session_ids already
    present in chat_messages, not a separate table. A session exists the
    moment its first message is sent."""
    rows = (
        db.query(
            ChatMessage.session_id,
            func.count(ChatMessage.id).label("message_count"),
            func.max(ChatMessage.created_at).label("last_message_at"),
        )
        .group_by(ChatMessage.session_id)
        .order_by(func.max(ChatMessage.created_at).desc())
        .all()
    )
    summaries = []
    for session_id, message_count, last_message_at in rows:
        last_message = (
            db.query(ChatMessage)
            .filter_by(session_id=session_id)
            .order_by(ChatMessage.created_at.desc())
            .first()
        )
        preview = (last_message.content[:PREVIEW_LENGTH] if last_message else "") or ""
        summaries.append(
            ChatSessionSummary(
                session_id=session_id,
                message_count=message_count,
                last_message_at=last_message_at,
                preview=preview,
            )
        )
    return summaries


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: Session = Depends(get_db)):
    db.query(ChatMessage).filter_by(session_id=session_id).delete()
    db.commit()
    return None


@router.get("/messages", response_model=list[ChatMessageRead])
def list_messages(session_id: str = "default", limit: int = 200, db: Session = Depends(get_db)):
    return (
        db.query(ChatMessage)
        .filter_by(session_id=session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )


@router.post("/messages", response_model=ChatSendResponse)
def send_message(payload: ChatSendRequest, db: Session = Depends(get_db)):
    session_id = payload.session_id.strip() or "default"
    message_text = payload.message.strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    user_message = ChatMessage(session_id=session_id, role="user", content=message_text)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    # Full persisted history for this session (including the message
    # just added) becomes the conversation sent to Ollama -- this is
    # what makes the chat "remember" earlier turns, not just the latest
    # message.
    history = (
        db.query(ChatMessage)
        .filter_by(session_id=session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    base_prompt = ollama_client.get_active_prompt(db, "main_chef") or ""
    # The user's own message doubles as the knowledge-retrieval query
    # (2026-07-31) -- see chat_service.build_chat_context's docstring.
    context = chat_service.build_chat_context(db, query=message_text)
    system_prompt = chat_service.build_chat_system_prompt(base_prompt, context)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m.role, "content": m.content} for m in history if m.role in ("user", "assistant"))

    try:
        response = ollama_client.chat(db, messages)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}") from exc
    raw_output = response.get("message", {}).get("content", "") if isinstance(response, dict) else str(response)

    parsed = chat_service.parse_chat_response(raw_output)
    assistant_message = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=parsed["reply"],
        actions=parsed["actions"] or None,
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    return ChatSendResponse(user_message=user_message, assistant_message=assistant_message)
