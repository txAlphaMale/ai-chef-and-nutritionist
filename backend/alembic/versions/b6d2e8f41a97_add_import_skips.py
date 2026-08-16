"""add import_skips so a failed URL is not re-fetched every batch

Revision ID: b6d2e8f41a97
Revises: f8c3d1a67b40
Create Date: 2026-08-07

already_imported_urls only knows about saved recipes, so a 404, a 403, a
dead domain or a page with no ingredients left no trace and came back in
every future batch. Measured on a real 478-URL export: batch 1 attempted
40 and saved 21; batch 2 attempted 40 and saved 8, because roughly
nineteen of its attempts were batch 1's failures fetched again. Dead URLs
sit earlier in the file, so each later batch would have been more of them
and less new work.

url_key holds the normalized form (bookmark_import_service.normalize_url)
so this table and the already-imported check agree about what "the same
page" means. `permanent` separates a 404 -- not coming back -- from a DNS
failure or a model timeout, which might be this evening's network.
"""

import sqlalchemy as sa
from alembic import op

revision = "b6d2e8f41a97"
down_revision = "f8c3d1a67b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_skips",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url_key", sa.String(length=1000), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("permanent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url_key"),
    )
    op.create_index("ix_import_skips_url_key", "import_skips", ["url_key"])


def downgrade() -> None:
    op.drop_index("ix_import_skips_url_key", table_name="import_skips")
    op.drop_table("import_skips")
