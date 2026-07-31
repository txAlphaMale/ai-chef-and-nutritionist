"""add recipe nutrition provenance column

Revision ID: 8f2c4b6a1d9e
Revises: 30de1d21d026
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f2c4b6a1d9e'
down_revision: Union[str, None] = '30de1d21d026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B1.2 -- distinguishes a recipe's Recipe.nutrition block that
    # was actually summed from resolved USDA/OFF ingredient data
    # ("computed"/"partial") from one that's purely an LLM guess or manual
    # entry ("ai_estimated"). Nullable: existing rows get NULL (treated as
    # "ai_estimated, never checked" by the app) rather than a backfilled
    # guess about their own provenance.
    with op.batch_alter_table("recipes") as batch_op:
        batch_op.add_column(sa.Column("nutrition_provenance", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recipes") as batch_op:
        batch_op.drop_column("nutrition_provenance")
