"""Thin wrapper around tavily-python, configured from the DB-backed
(encrypted) API key rather than a static env var."""
from __future__ import annotations

from sqlalchemy.orm import Session
from tavily import TavilyClient

from app.services import settings_service


def _client(db: Session) -> TavilyClient | None:
    api_key = settings_service.get_setting(db, "tavily_api_key")
    if not api_key:
        return None
    return TavilyClient(api_key=api_key)


def search(db: Session, query: str, max_results: int = 5) -> dict | None:
    """Returns Tavily's raw response dict, or None if no API key is
    configured yet -- callers should treat that as "web search
    unavailable", not an error."""
    client = _client(db)
    if client is None:
        return None
    return client.search(query=query, max_results=max_results)


def is_configured(db: Session) -> bool:
    return bool(settings_service.get_setting(db, "tavily_api_key"))
