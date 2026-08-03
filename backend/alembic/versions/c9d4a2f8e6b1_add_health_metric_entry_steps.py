"""add health metric entry steps

Revision ID: c9d4a2f8e6b1
Revises: b3f7e9c2a5d1
Create Date: 2026-08-02 00:00:00.000000

Backlog B8.2 -- see app/models/health.py's HealthMetricEntry docstring
comment on `steps` for the full rationale (a single daily step total,
not a fuller time-series activity model).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d4a2f8e6b1'
down_revision: Union[str, None] = 'b3f7e9c2a5d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("health_metric_entries") as batch_op:
        batch_op.add_column(sa.Column("steps", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_metric_entries") as batch_op:
        batch_op.drop_column("steps")
