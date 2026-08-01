"""add meal plan entry leftover link

Revision ID: 470cd22a681e
Revises: f3b8d0a6c1e4
Create Date: 2026-08-01 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '470cd22a681e'
down_revision: Union[str, None] = 'f3b8d0a6c1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite can't ALTER TABLE to add a foreign key constraint directly --
    # batch mode (copy-and-move) is required, same as the recipe variant
    # self-link migration (349c7fd3ed4c) this mirrors.
    with op.batch_alter_table("meal_plan_entries") as batch_op:
        batch_op.add_column(sa.Column("leftover_of_entry_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_meal_plan_entries_leftover_of_entry_id_meal_plan_entries",
            "meal_plan_entries",
            ["leftover_of_entry_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("meal_plan_entries") as batch_op:
        batch_op.drop_constraint(
            "fk_meal_plan_entries_leftover_of_entry_id_meal_plan_entries", type_="foreignkey"
        )
        batch_op.drop_column("leftover_of_entry_id")
