"""Pantry / fridge / freezer / produce / spice inventory tracking."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UtcDateTime, utc_now


class InventoryItem(Base, TimestampMixin):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # pantry | fridge | freezer | produce | spice | other
    category: Mapped[str] = mapped_column(String(50), default="pantry")

    # --- Quantity model ------------------------------------------------
    #
    # `quantity` is the CURRENT ON-HAND amount, expressed in `unit` --
    # the number every deduction and every urgency calculation reads and
    # mutates. `unit` must be a real convertible measurement unit (one of
    # unit_conversion_service's canonical units, or the literal "count"
    # for items with no meaningful sub-unit), NOT a compound string like
    # "8 oz bag" mixing a measurement with a container description.
    #
    # That distinction is the whole point of the package_* fields below,
    # and getting it wrong breaks two things quietly: normalize_unit does
    # not recognise "24 oz pack" as a unit, so a recipe asking for "4 oz
    # bacon" cannot convert against it; and cost math needs a stable
    # denominator, which a container word cannot provide.
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Immutable snapshot of `quantity` (in `unit`) at the moment this row
    # was created/purchased -- i.e. "volume at time of purchase", never
    # decremented by usage the way `quantity` ("volume on hand") is.
    # This is what cost_service now divides a recorded `unit_price` by,
    # instead of the live, shrinking `quantity` -- so cost-per-serving
    # stays correct as a package gets used up rather than silently
    # rising. Nullable for two reasons: (a) rows created before this
    # column existed have no honest value to backfill beyond "assume
    # nothing has been used yet" (see this session's migration for
    # exactly what it does and doesn't assume), and (b) intake sources
    # with no real "purchase" concept (a bare pantry-snapshot vision
    # photo) can legitimately leave it unset. `routers/inventory.py`'s
    # create endpoint defaults it to the row's own initial `quantity`
    # whenever the caller doesn't supply one explicitly.
    purchased_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Purely descriptive package/container metadata -- NEVER read by any
    # arithmetic (deduction, cost, unit conversion). Exists so a
    # household can still see "2 x 14 oz cans" on the item without that
    # text ever being the thing `unit`/`quantity` have to encode.
    # `package_quantity` is how much of `unit` is in ONE package (e.g. 8
    # for an "8 oz bag"); `package_count` is how many packages this row
    # represents (e.g. 2 for "2 cans"); on creation, when both are
    # known, the frontend computes the initial `quantity` as their
    # product, then the household can still hand-adjust it (e.g. adding
    # an already-opened item). `package_descriptor` is the leftover
    # container word ("bag", "can", "bottle", ...), free text, shown for
    # reference only. All three nullable -- an item with no package
    # concept at all (loose produce weighed at checkout) simply leaves
    # them unset and behaves exactly as `quantity`/`unit` alone always
    # did. See app/services/package_parsing.py for the shared best-
    # effort parser that fills these in from freeform text (Open Food
    # Facts' quantity field, a spreadsheet's unit column) when possible.
    package_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    package_count: Mapped[float | None] = mapped_column(Float, nullable=True)
    package_descriptor: Mapped[str | None] = mapped_column(String(50), nullable=True)

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
    # import_order_history | barcode -- how the item entered inventory
    source: Mapped[str] = mapped_column(String(20), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngredientAlias(Base, TimestampMixin):
    """Audit P1-5: one remembered answer to "which inventory item did you
    mean by this name".

    The matcher in `app.services.ingredient_resolution_service` is
    deliberately conservative -- it refuses to guess between "chicken" and
    "chicken breast" rather than silently picking one, because picking
    wrong writes a bad number into `inventory_items.quantity` with nothing
    on screen to notice. That conservatism is only liveable if the user is
    asked at most ONCE per name. This table is where that answer lives.

    `alias_normalized` is the matcher's own normalised form of what the
    user typed (see `normalize_name`), not the raw text -- so "Chopped
    Tomatoes", "chopped tomato" and "CHOPPED TOMATOES" all hit the same
    alias without three separate rows. `alias_text` keeps the raw form for
    display in the Settings alias list, because "you taught me
    tomatos -> Roma tomatoes" is more meaningful to a human than the
    folded token string.

    Two targets, and the distinction matters:

    - `canonical_name` (required) is the DURABLE target. Aliases resolve
      to a NAME and the name is then matched normally, which means the
      alias keeps working after the matched row is used up, deleted and
      re-bought -- which is the normal life of a grocery item and would
      otherwise rot every alias within a shopping cycle.
    - `inventory_item_id` (optional) pins the answer to one specific row,
      for the case where a household genuinely keeps two rows whose names
      normalise identically. `ondelete="SET NULL"` rather than CASCADE:
      when that row goes away the alias should fall back to resolving by
      `canonical_name`, not vanish. (Note this FK is only enforced because
      audit P2-1 turned `PRAGMA foreign_keys` on -- before that fix every
      FK in this schema was decorative.)

    `source` distinguishes a household's own correction ("user") from
    anything the app itself ever writes, so a future cleanup or export can
    tell them apart. Nothing seeds this table today: shipping a starter
    alias list would mean asserting food equivalences this project has not
    verified, which is exactly the guessing the matcher exists to avoid.
    """

    __tablename__ = "ingredient_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    alias_normalized: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    alias_text: Mapped[str] = mapped_column(String(200))
    canonical_name: Mapped[str] = mapped_column(String(200))
    inventory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True
    )
    # "user" -- the household confirmed this from a disambiguation prompt
    source: Mapped[str] = mapped_column(String(20), default="user")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, nullable=False)


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
    last_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_check_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
