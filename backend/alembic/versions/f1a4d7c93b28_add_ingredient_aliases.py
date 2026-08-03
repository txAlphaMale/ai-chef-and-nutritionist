"""add ingredient aliases

Revision ID: f1a4d7c93b28
Revises: c9d4a2f8e6b1
Create Date: 2026-08-03 00:00:00.000000

Audit finding P1-5 -- see app/models/inventory.py's IngredientAlias
docstring and app/services/ingredient_resolution_service.py's module
docstring for why the resolution layer needs a persisted alias table at
all (the matcher refuses to guess between near-matches, which is only
liveable if each refusal is answered at most once).

Creates the table only. Deliberately seeds nothing: a starter alias list
would be this project asserting food equivalences it has not verified.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a4d7c93b28'
down_revision: Union[str, None] = 'c9d4a2f8e6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingredient_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alias_normalized", sa.String(length=200), nullable=False),
        sa.Column("alias_text", sa.String(length=200), nullable=False),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        # SET NULL, not CASCADE: when the pinned row is used up and
        # deleted the alias should fall back to resolving by
        # canonical_name, not disappear along with it.
        sa.Column("inventory_item_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="user"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="fk_ingredient_aliases_inventory_item_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingredient_aliases_alias_normalized",
        "ingredient_aliases",
        ["alias_normalized"],
        unique=True,
    )

    # Reconciliation provenance on the grocery list -- see
    # app/models/meal_plan.py's GroceryListItem for what each one holds.
    # `needs_review` in particular closes a gap left by audit P1-4: the
    # explanation for why an unconvertible-unit line kept its full
    # quantity was already being computed, and then discarded before it
    # ever reached a persisted row, so no user has ever seen one.
    with op.batch_alter_table("grocery_list_items") as batch_op:
        batch_op.add_column(sa.Column("needs_review", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("matched_item_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("match_confidence", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("grocery_list_items") as batch_op:
        batch_op.drop_column("match_confidence")
        batch_op.drop_column("matched_item_name")
        batch_op.drop_column("needs_review")
    op.drop_index("ix_ingredient_aliases_alias_normalized", table_name="ingredient_aliases")
    op.drop_table("ingredient_aliases")
