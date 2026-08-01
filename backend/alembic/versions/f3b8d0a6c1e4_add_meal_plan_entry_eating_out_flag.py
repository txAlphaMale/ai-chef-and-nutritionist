"""add meal plan entry eating out flag

Revision ID: f3b8d0a6c1e4
Revises: e7a1c9f4b2d6
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3b8d0a6c1e4'
down_revision: Union[str, None] = 'e7a1c9f4b2d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B10.1 -- marks a recipe-less meal-plan slot as "eating out"
    # rather than an unplanned/forgotten one. Purely descriptive; a
    # recipe_id=None entry already confirms without inventory deduction
    # and is already excluded from grocery-list aggregation and the
    # nutrition summary (both pre-existing behaviors, unchanged here).
    with op.batch_alter_table("meal_plan_entries") as batch_op:
        batch_op.add_column(sa.Column("is_eating_out", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("meal_plan_entries") as batch_op:
        batch_op.drop_column("is_eating_out")
