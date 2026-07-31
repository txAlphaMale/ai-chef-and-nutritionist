"""add recipe ingredient density for weight-mode unit conversion

Revision ID: d4f9c2a8e1b3
Revises: c3d8a1f5b7e2
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f9c2a8e1b3'
down_revision: Union[str, None] = 'c3d8a1f5b7e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B10.5 -- implied density (g/mL) parsed from USDA foodPortions
    # data at resolution time, letting the unit-system display toggle
    # bridge volume<->mass ("weight mode") for ingredients that resolved
    # against a food reporting a volume-unit portion. Nullable: most
    # existing resolved ingredients have no density yet until re-resolved,
    # and many foods never report a volume-unit portion at all -- see
    # app/services/food_data_service.py's module docstring.
    with op.batch_alter_table("recipe_ingredients") as batch_op:
        batch_op.add_column(sa.Column("density_g_per_ml", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recipe_ingredients") as batch_op:
        batch_op.drop_column("density_g_per_ml")
