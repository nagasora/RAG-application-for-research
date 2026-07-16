"""Add workspace-scoped research questions and named source sets.

Revision ID: 20260716_0014
Revises: 20260716_0013
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_0014"
down_revision = "20260716_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_questions_workspace_id", "research_questions", ["workspace_id"])
    op.create_index("ix_research_questions_created_by", "research_questions", ["created_by"])
    op.create_index("ix_research_questions_workspace_updated", "research_questions", ["workspace_id", "updated_at"])
    op.create_table(
        "source_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_sets_workspace_id", "source_sets", ["workspace_id"])
    op.create_index("ix_source_sets_created_by", "source_sets", ["created_by"])
    op.create_index("ix_source_sets_workspace_updated", "source_sets", ["workspace_id", "updated_at"])
    op.create_table(
        "source_set_papers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_set_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["source_set_id"], ["source_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_set_id", "paper_id", name="uq_source_set_papers_set_paper"),
    )
    op.create_index("ix_source_set_papers_source_set_id", "source_set_papers", ["source_set_id"])
    op.create_index("ix_source_set_papers_paper", "source_set_papers", ["paper_id"])


def downgrade() -> None:
    op.drop_index("ix_source_set_papers_paper", table_name="source_set_papers")
    op.drop_index("ix_source_set_papers_source_set_id", table_name="source_set_papers")
    op.drop_table("source_set_papers")
    op.drop_index("ix_source_sets_workspace_updated", table_name="source_sets")
    op.drop_index("ix_source_sets_created_by", table_name="source_sets")
    op.drop_index("ix_source_sets_workspace_id", table_name="source_sets")
    op.drop_table("source_sets")
    op.drop_index("ix_research_questions_workspace_updated", table_name="research_questions")
    op.drop_index("ix_research_questions_created_by", table_name="research_questions")
    op.drop_index("ix_research_questions_workspace_id", table_name="research_questions")
    op.drop_table("research_questions")
