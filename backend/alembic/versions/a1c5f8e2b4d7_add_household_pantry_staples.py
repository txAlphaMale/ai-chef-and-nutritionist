"""add household pantry staples

Revision ID: a1c5f8e2b4d7
Revises: b7e2f4a9d3c8
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c5f8e2b4d7'
down_revision: Union[str, None] = 'b7e2f4a9d3c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B5.5 -- a household-maintained "always on hand" ingredient
    # name list (see app/services/meal_plan_service.is_pantry_staple).
    # Non-nullable JSON defaulting to an empty list so existing rows come
    # back as "no staples configured" rather than erroring.
    with op.batch_alter_table("household_preferences") as batch_op:
        batch_op.add_column(sa.Column("pantry_staples", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("household_preferences") as batch_op:
        batch_op.drop_column("pantry_staples")
