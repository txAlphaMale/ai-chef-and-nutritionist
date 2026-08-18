"""Add household_members.birth_date.

Author-requested 2026-08-18. A stored AGE is wrong the day after it is
entered and nothing in the app would ever say so -- it silently skews the
DRI targets that are the reason the field is collected at all. A birth date
does not go stale.

`age` is deliberately KEPT and not backfilled. Backfilling would mean
inventing a birth date from an age, which is a fabricated fact of exactly
the kind this project refuses elsewhere (see the nutrition-provenance and
allergen work). Existing members keep working through the legacy fallback
in app/services/household_age.py until somebody edits them.

Revision ID: d5b8e3c17a92
Revises: c4f7a2e910bd
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5b8e3c17a92"
down_revision: Union[str, None] = "c4f7a2e910bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("household_members", sa.Column("birth_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("household_members", "birth_date")
