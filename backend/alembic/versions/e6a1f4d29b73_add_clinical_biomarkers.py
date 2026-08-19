"""Add ApoB, Lp(a), HbA1c and waist circumference to health metrics.

Backlog B18.1 (2026-08-18). The 2026 ACC/AHA multi-society dyslipidemia
guideline names apolipoprotein B and lipoprotein(a) as measurements that
change risk assessment; HbA1c and waist circumference are the two other
values a household working on cholesterol is routinely handed. None of
them had anywhere to live in an app whose stated purpose is LDL reduction.

Lp(a) gets a value AND a unit column, deliberately. It is reported in both
mg/dL and nmol/L and the two are not reliably interconvertible (the factor
depends on apo(a) isoform size), so a bare number would silently mix two
scales in one trend line.

Revision ID: e6a1f4d29b73
Revises: d5b8e3c17a92
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6a1f4d29b73"
down_revision: Union[str, None] = "d5b8e3c17a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("health_metric_entries", sa.Column("apob_mg_dl", sa.Float(), nullable=True))
    op.add_column("health_metric_entries", sa.Column("lpa_value", sa.Float(), nullable=True))
    op.add_column("health_metric_entries", sa.Column("lpa_unit", sa.String(length=10), nullable=True))
    op.add_column("health_metric_entries", sa.Column("hba1c_percent", sa.Float(), nullable=True))
    op.add_column("health_metric_entries", sa.Column("waist_cm", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("health_metric_entries", "waist_cm")
    op.drop_column("health_metric_entries", "hba1c_percent")
    op.drop_column("health_metric_entries", "lpa_unit")
    op.drop_column("health_metric_entries", "lpa_value")
    op.drop_column("health_metric_entries", "apob_mg_dl")
