"""add household dietary pattern

Revision ID: b7e2f4a9d3c8
Revises: 470cd22a681e
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2f4a9d3c8'
down_revision: Union[str, None] = '470cd22a681e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B2.3 -- a selectable, structured dietary-goal preset (see
    # app/services/dietary_pattern_service.py) that concretely biases
    # meal-plan generation, distinct from the existing free-text `goals`
    # column. Nullable, no default preset selected.
    with op.batch_alter_table("household_preferences") as batch_op:
        batch_op.add_column(sa.Column("dietary_pattern", sa.String(length=30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("household_preferences") as batch_op:
        batch_op.drop_column("dietary_pattern")
