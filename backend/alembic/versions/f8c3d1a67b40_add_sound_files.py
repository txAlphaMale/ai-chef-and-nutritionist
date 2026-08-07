"""add sound_files for the cooking-timer sound library

Revision ID: f8c3d1a67b40
Revises: a3e7b1c05d94
Create Date: 2026-08-07

The rows are an index into files on the data volume, not the audio
itself -- same arrangement as knowledge_files and recipe images. Built-in
rows are re-seeded on every boot from sound_service.BUILTIN_SOUNDS, so an
install that loses this table's contents recovers its library; one that
loses the files regenerates them.
"""

import sqlalchemy as sa
from alembic import op

revision = "f8c3d1a67b40"
down_revision = "a3e7b1c05d94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sound_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )


def downgrade() -> None:
    op.drop_table("sound_files")
