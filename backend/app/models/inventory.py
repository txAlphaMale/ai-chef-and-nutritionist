"""Pantry / fridge / freezer / produce / spice inventory tracking."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class InventoryItem(Base, TimestampMixin):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # pantry | fridge | freezer | produce | spice | other
    category: Mapped[str] = mapped_column(String(50), default="pantry")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)

    purchased_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_used_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # "use this up" boost -- e.g. 5 lbs of lentils the household wants
    # worked into meals more often (but not constantly)
    is_priority: Mapped[bool] = mapped_column(Boolean, default=False)
    priority_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Backlog B10.3 (2026-08-01) -- price actually paid for this quantity,
    # as purchased (not a per-recipe/per-serving cost, that's B6.1's job
    # once it exists). Nullable: most intake sources (manual entry, the
    # pantry-photo vision snapshot, the AI receipt/list import) still have
    # no price signal at all. Populated today only by the order-history
    # CSV/XLSX importer below, where a price column is the norm.
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # manual | vision | chat | import_photo | import_pdf | import_text |
    # import_order_history -- how the item entered inventory
    source: Mapped[str] = mapped_column(String(20), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderImportProfile(Base):
    """Backlog B10.3 (2026-08-01): a user-saved column-mapping for the
    generic order-history CSV/XLSX importer, so a household doesn't have
    to re-map "which column is the item name" every time they import a
    fresh export from the same source (e.g. a Walmart order-history
    export from a browser extension).

    Deliberately NOT seeded with any pre-built retailer profile (no
    "Walmart" preset shipped) -- Walmart publishes no official
    consumer export, so every real-world export comes from a third-party
    browser extension, each with its own, unverified, and mutable column
    layout (checked live during research: none publish a stable
    documented schema). Shipping a guessed set of column names would
    violate this app's own "never guess, verify or say so" discipline.
    The user creates and saves their own mapping from their real file's
    actual headers on first use instead -- see order_import_service.py
    and routers/inventory.py's order-import endpoints."""

    __tablename__ = "order_import_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name_column: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity_column: Mapped[str | None] = mapped_column(String(200), nullable=True)
    unit_column: Mapped[str | None] = mapped_column(String(200), nullable=True)
    price_column: Mapped[str | None] = mapped_column(String(200), nullable=True)
    date_column: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
