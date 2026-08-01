"""add meal plan entry google event id

Revision ID: c8e1a4f0d5b2
Revises: a1c5f8e2b4d7
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e1a4f0d5b2'
down_revision: Union[str, None] = 'a1c5f8e2b4d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B12.1 -- tracks which Google Calendar event (in the
    # household's dedicated "Chef Meal Plan" calendar) corresponds to
    # this entry, so push-sync can update/delete the right event instead
    # of creating duplicates. Nullable: most entries either predate this
    # feature or were created while sync was off.
    with op.batch_alter_table("meal_plan_entries") as batch_op:
        batch_op.add_column(sa.Column("google_event_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("meal_plan_entries") as batch_op:
        batch_op.drop_column("google_event_id")
