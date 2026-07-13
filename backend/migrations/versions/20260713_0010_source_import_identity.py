"""Make parsed-source identity include kind and locator.

Revision ID: 20260713_0010
Revises: 20260713_0009
"""

from alembic import op


revision = "20260713_0010"
down_revision = "20260713_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_versions") as batch_op:
        batch_op.drop_constraint("uq_source_versions_workspace_content_hash", type_="unique")
        batch_op.create_unique_constraint(
            "uq_source_versions_workspace_kind_locator_content_hash",
            ["workspace_id", "kind", "locator", "content_hash"],
        )


def downgrade() -> None:
    with op.batch_alter_table("source_versions") as batch_op:
        batch_op.drop_constraint("uq_source_versions_workspace_kind_locator_content_hash", type_="unique")
        batch_op.create_unique_constraint(
            "uq_source_versions_workspace_content_hash",
            ["workspace_id", "content_hash"],
        )
