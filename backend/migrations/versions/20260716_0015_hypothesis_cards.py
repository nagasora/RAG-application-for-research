"""Add structured, reviewable hypothesis cards.

Revision ID: 20260716_0015
Revises: 20260716_0014
"""
from alembic import op
import sqlalchemy as sa

revision = "20260716_0015"
down_revision = "20260716_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hypothesis_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("claim", sa.Text(), nullable=False), sa.Column("mechanism", sa.Text(), nullable=False, server_default=""),
        sa.Column("target", sa.Text(), nullable=False, server_default=""), sa.Column("conditions", sa.Text(), nullable=False, server_default=""),
        sa.Column("intervention", sa.Text(), nullable=False, server_default=""), sa.Column("outcome", sa.Text(), nullable=False, server_default=""),
        sa.Column("direction", sa.String(64), nullable=False, server_default=""),
        sa.Column("assumptions", sa.JSON(), nullable=False), sa.Column("competing_theories", sa.JSON(), nullable=False),
        sa.Column("predictions", sa.JSON(), nullable=False), sa.Column("falsifiers", sa.JSON(), nullable=False),
        sa.Column("test", sa.Text(), nullable=False, server_default=""), sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("human_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("empirically_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_hypothesis_cards_workspace_id", "hypothesis_cards", ["workspace_id"])
    op.create_index("ix_hypothesis_cards_created_by", "hypothesis_cards", ["created_by"])
    op.create_index("ix_hypothesis_cards_workspace_updated", "hypothesis_cards", ["workspace_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_hypothesis_cards_workspace_updated", table_name="hypothesis_cards")
    op.drop_index("ix_hypothesis_cards_created_by", table_name="hypothesis_cards")
    op.drop_index("ix_hypothesis_cards_workspace_id", table_name="hypothesis_cards")
    op.drop_table("hypothesis_cards")
