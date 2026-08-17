"""Add app_log_entries -- the database-backed application log.

Backlog B24.2 (capstone review 2026-08-16). Before this, the backend had
no logging configuration at all: 36 bare print() calls to container
stdout, nothing visible from inside the app, and every operational
question needing a shell on the host. See app/models/log.py for the full
rationale, including why stdout is kept alongside this table and what must
never be written into it.

Revision ID: c4f7a2e910bd
Revises: b6d2e8f41a97
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f7a2e910bd"
down_revision: Union[str, None] = "b6d2e8f41a97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_log_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_log_entries_created_at", "app_log_entries", ["created_at"])
    op.create_index("ix_app_log_entries_level", "app_log_entries", ["level"])
    op.create_index("ix_app_log_entries_source", "app_log_entries", ["source"])
    op.create_index("ix_app_log_entries_job_id", "app_log_entries", ["job_id"])
    # The Logs view's default query, as one index scan.
    op.create_index("ix_app_log_entries_created_level", "app_log_entries", ["created_at", "level"])


def downgrade() -> None:
    op.drop_index("ix_app_log_entries_created_level", table_name="app_log_entries")
    op.drop_index("ix_app_log_entries_job_id", table_name="app_log_entries")
    op.drop_index("ix_app_log_entries_source", table_name="app_log_entries")
    op.drop_index("ix_app_log_entries_level", table_name="app_log_entries")
    op.drop_index("ix_app_log_entries_created_at", table_name="app_log_entries")
    op.drop_table("app_log_entries")
