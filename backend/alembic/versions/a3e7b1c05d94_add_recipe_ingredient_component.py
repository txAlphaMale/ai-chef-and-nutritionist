"""add recipe ingredient component

Revision ID: a3e7b1c05d94
Revises: f1a4d7c93b28
Create Date: 2026-08-03 00:00:00.000000

Lets one recipe represent a dish built from named parts -- a crust and a
filling, a marinade and a sauce. Before this, RecipeIngredient had no way
to say which part a line belonged to, so two lines with the same name in
different sections were indistinguishable and the import prompt was left
trying to keep them apart by wording alone. It could not: importing Bon
Appetit's Pumpkin Chiffon Pie, the crust's 2 Tbsp. sugar came through as
0.5 cup, taken from a sentence in the crust's method.

Nullable, and nothing backfills it. An existing row genuinely has no
component -- inventing one would be asserting structure the source never
stated. None is the correct value for every recipe imported before this,
and for every single-component recipe after it.

See app/models/recipe.py's RecipeIngredient.component for why consumers
doing quantity math must aggregate across this column rather than group
by it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a3e7b1c05d94"
down_revision: Union[str, None] = "f1a4d7c93b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recipe_ingredients",
        sa.Column("component", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recipe_ingredients", "component")
