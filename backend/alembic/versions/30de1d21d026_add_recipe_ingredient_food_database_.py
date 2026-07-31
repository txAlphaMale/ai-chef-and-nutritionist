"""add recipe ingredient food database resolution columns

Revision ID: 30de1d21d026
Revises: 349c7fd3ed4c
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '30de1d21d026'
down_revision: Union[str, None] = '349c7fd3ed4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backlog B1.1 -- caches the result of resolving a recipe ingredient
    # against USDA FoodData Central / Open Food Facts, so a given
    # ingredient is only looked up once (see app/services/
    # food_data_service.py). Plain nullable columns, no FK -- fdc_id/
    # off_barcode are identifiers in an external system, not local rows.
    with op.batch_alter_table("recipe_ingredients") as batch_op:
        batch_op.add_column(sa.Column("fdc_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("off_barcode", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("resolved_food_name", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("resolution_source", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("nutrition_per_100g", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recipe_ingredients") as batch_op:
        batch_op.drop_column("resolved_at")
        batch_op.drop_column("nutrition_per_100g")
        batch_op.drop_column("resolution_source")
        batch_op.drop_column("resolved_food_name")
        batch_op.drop_column("off_barcode")
        batch_op.drop_column("fdc_id")
