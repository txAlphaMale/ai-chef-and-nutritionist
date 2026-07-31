"""add inventory unit price and order import profiles

Revision ID: e7a1c9f4b2d6
Revises: d4f9c2a8e1b3
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a1c9f4b2d6'
down_revision: Union[str, None] = 'd4f9c2a8e1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B10.3 -- price actually paid for a purchased quantity.
    # Nullable: most intake sources have no price signal; today only the
    # new order-history CSV/XLSX importer populates it.
    with op.batch_alter_table("inventory_items") as batch_op:
        batch_op.add_column(sa.Column("unit_price", sa.Float(), nullable=True))

    # Backlog B10.3 -- user-saved column-mapping profiles for the generic
    # order-history importer (e.g. "Walmart"), so a household doesn't
    # have to re-map columns on every re-export. Deliberately not seeded
    # with any pre-built retailer profile -- see OrderImportProfile's
    # docstring in app/models/inventory.py for why.
    op.create_table(
        "order_import_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("name_column", sa.String(length=200), nullable=True),
        sa.Column("quantity_column", sa.String(length=200), nullable=True),
        sa.Column("unit_column", sa.String(length=200), nullable=True),
        sa.Column("price_column", sa.String(length=200), nullable=True),
        sa.Column("date_column", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_order_import_profiles_name", "order_import_profiles", ["name"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_order_import_profiles_name", table_name="order_import_profiles")
    op.drop_table("order_import_profiles")
    with op.batch_alter_table("inventory_items") as batch_op:
        batch_op.drop_column("unit_price")
