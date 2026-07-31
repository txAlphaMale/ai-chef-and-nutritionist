"""add allergen restrictions and gluten observance level

Revision ID: c3d8a1f5b7e2
Revises: 8f2c4b6a1d9e
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d8a1f5b7e2'
down_revision: Union[str, None] = '8f2c4b6a1d9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B3.1/B3.2 -- a structured allergen taxonomy the app can
    # deterministically check ingredient names against (see
    # app/services/allergen_service.py), plus a gluten cross-contact
    # observance level. Both nullable/default-empty so existing rows
    # come back as "no restrictions configured" rather than erroring.
    with op.batch_alter_table("household_preferences") as batch_op:
        batch_op.add_column(sa.Column("restricted_allergens", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("gluten_observance_level", sa.String(length=30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("household_preferences") as batch_op:
        batch_op.drop_column("gluten_observance_level")
        batch_op.drop_column("restricted_allergens")
