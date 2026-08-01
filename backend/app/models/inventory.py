"""Pantry / fridge / freezer / produce / spice inventory tracking."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
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


class RecallAlert(Base, TimestampMixin):
    """Backlog B3.3: one persisted, dismissible alert for a single
    recall/public-health-alert record that matched a current (or
    recently seen) inventory item name, from either the USDA FSIS Recall
    API or the openFDA food enforcement API -- see
    app.services.recall_service for the fetch/matching logic and why
    both sources are queried (FSIS covers meat/poultry/egg products,
    which openFDA's food endpoint does NOT include; openFDA covers
    everything else FDA-regulated).

    Persisted (not just an in-memory response to a check) so a match
    survives across checks and can be individually dismissed -- an
    un-dismissed match keeps counting toward the RecallBanner even if a
    later check doesn't re-fetch it (recall_service only re-runs the
    check at most once per throttle interval, see RecallCheckState), and
    a dismissed one stays dismissed rather than reappearing on the very
    next check just because the same recall record is still published.
    """

    __tablename__ = "recall_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # "fsis" | "openfda"
    source: Mapped[str] = mapped_column(String(20))
    # The recall's own identifier from its source (FSIS field_recall_number,
    # openFDA recall_number) -- paired with `source` for dedup, since the
    # two agencies don't share an identifier namespace.
    external_id: Mapped[str] = mapped_column(String(100), index=True)
    matched_item_name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Source's own status text, not normalized to a fixed vocabulary --
    # FSIS uses "Active"/"Closed" (derived from field_active_notice),
    # openFDA uses "Ongoing"/"Terminated"/"Completed" natively. Shown
    # as-is rather than mapped onto one shared vocabulary, since
    # collapsing them risks implying a precision neither source actually
    # offers about what "active" means for a product already off shelves.
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    recall_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    states: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)


class RecallCheckState(Base):
    """Backlog B3.3: a singleton (always id=1) row tracking when the
    recall check last actually ran, so the fast, DB-only GET endpoint
    the frontend banner polls doesn't itself trigger a live FSIS/openFDA
    fetch on every page view -- see recall_service.check_inventory_for_
    recalls's docstring for the full throttling rationale (Chef has no
    real background cron scheduler; this is the closest honest
    approximation of the backlog's original "daily/weekly check" framing
    without inventing one)."""

    __tablename__ = "recall_check_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_check_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
