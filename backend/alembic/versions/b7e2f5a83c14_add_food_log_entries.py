"""Add food_log_entries -- a record of what was actually eaten.

Backlog B17.1 (2026-08-20). Until now every nutrition figure in this app
described a plan rather than a person: the weekly roll-up, the
diet-quality approximation and the health-trend loop all read the meal
plan and reported it as intake.

Notes on the choices this migration encodes:

* **Every foreign key is SET NULL, never CASCADE.** A log row is a
  historical fact. Deleting a recipe, or removing somebody from the
  household, must not rewrite what was eaten -- the description and the
  nutrition are stored on the row precisely so it survives losing its
  links.
* **`meal_plan_entry_id` is UNIQUE.** One confirmed plan slot may produce
  at most one automatic log row, whatever future code does to the confirm
  path. SQLite exempts NULLs from uniqueness, which is exactly the
  wanted behaviour: manual rows all carry NULL here.
* **`eaten_at` is indexed and is not `created_at`.** Logging yesterday's
  dinner is normal, and every roll-up groups by when the food was eaten.
* Nothing is backfilled. There is no honest way to invent an eating
  history, and a confirmed plan entry from last month is evidence that a
  meal was cooked, not a record of who ate it or when.

Revision ID: b7e2f5a83c14
Revises: a2d9c6f14e83
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2f5a83c14"
down_revision: Union[str, None] = "a2d9c6f14e83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "food_log_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("eaten_at", sa.DateTime(), nullable=False),
        sa.Column("meal_type", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=True),
        sa.Column("inventory_item_id", sa.Integer(), nullable=True),
        sa.Column("meal_plan_entry_id", sa.Integer(), nullable=True),
        sa.Column("servings", sa.Float(), nullable=False),
        sa.Column("nutrition", sa.JSON(), nullable=True),
        sa.Column("nutrition_provenance", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["household_members.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["inventory_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["meal_plan_entry_id"], ["meal_plan_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meal_plan_entry_id", name="uq_food_log_meal_plan_entry"),
    )
    op.create_index("ix_food_log_entries_eaten_at", "food_log_entries", ["eaten_at"])
    op.create_index("ix_food_log_entries_member_id", "food_log_entries", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_food_log_entries_member_id", table_name="food_log_entries")
    op.drop_index("ix_food_log_entries_eaten_at", table_name="food_log_entries")
    op.drop_table("food_log_entries")
