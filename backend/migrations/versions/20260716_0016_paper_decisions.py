"""Add workspace paper screening decisions.

Revision ID: 20260716_0016
Revises: 20260716_0015
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_0016"
down_revision = "20260716_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_decisions",
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False, server_default="undecided"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('undecided', 'included', 'excluded')", name="ck_paper_decisions_decision"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("paper_id"),
    )
    op.create_index("ix_paper_decisions_workspace_id", "paper_decisions", ["workspace_id"])
    op.create_index("ix_paper_decisions_workspace_decision", "paper_decisions", ["workspace_id", "decision"])


def downgrade() -> None:
    op.drop_index("ix_paper_decisions_workspace_decision", table_name="paper_decisions")
    op.drop_index("ix_paper_decisions_workspace_id", table_name="paper_decisions")
    op.drop_table("paper_decisions")
