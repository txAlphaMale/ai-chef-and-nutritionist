"""Add NOVA group, Nutri-Score and the source barcode to inventory items.

Backlog B19.1 (2026-08-19). Open Food Facts returns both classifications
in the SAME product response the barcode scanner already fetches, so
capturing them costs no additional API call. They are the input to B19.2
(what share of a planned week is ultra-processed), a metric with real
evidence behind it that none of the commercial services in the capstone
benchmark surface.

Every column is nullable and nothing is backfilled, deliberately. Items
already in the pantry were entered by hand, by photo or by CSV import;
none of those carry a barcode, so there is no product identity to look a
classification up by, and inventing one would be a guess written into a
health metric. Items scanned from here on carry it. NULL means "we do not
know", never "unprocessed" -- B19.2 must report its own denominator.

Revision ID: a2d9c6f14e83
Revises: e6a1f4d29b73
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2d9c6f14e83"
down_revision: Union[str, None] = "e6a1f4d29b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("inventory_items", sa.Column("off_barcode", sa.String(length=32), nullable=True))
    op.add_column("inventory_items", sa.Column("nova_group", sa.Integer(), nullable=True))
    op.add_column("inventory_items", sa.Column("nutriscore_grade", sa.String(length=1), nullable=True))
    op.create_index("ix_inventory_items_off_barcode", "inventory_items", ["off_barcode"])


def downgrade() -> None:
    op.drop_index("ix_inventory_items_off_barcode", table_name="inventory_items")
    op.drop_column("inventory_items", "nutriscore_grade")
    op.drop_column("inventory_items", "nova_group")
    op.drop_column("inventory_items", "off_barcode")
