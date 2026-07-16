"""Add immutable research-run context and append-only artifacts.

Revision ID: 20260716_0018
Revises: 20260716_0017
"""
from alembic import op
import sqlalchemy as sa

revision = "20260716_0018"
down_revision = "20260716_0017"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("research_runs",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36)), sa.Column("research_question_id", sa.String(36)), sa.Column("source_set_id", sa.String(36)),
        sa.Column("research_question", sa.Text(), nullable=False, server_default=""), sa.Column("source_paper_ids", sa.JSON(), nullable=False),
        sa.Column("excluded_paper_ids", sa.JSON(), nullable=False), sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("success_criteria", sa.Text(), nullable=False, server_default=""), sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(255), nullable=False, server_default=""), sa.Column("prompt_version", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"), sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_research_runs_status"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["research_question_id"], ["research_questions.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["source_set_id"], ["source_sets.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_research_runs_workspace_id", "research_runs", ["workspace_id"])
    op.create_index("ix_research_runs_created_by", "research_runs", ["created_by"])
    op.create_index("ix_research_runs_research_question_id", "research_runs", ["research_question_id"])
    op.create_index("ix_research_runs_source_set_id", "research_runs", ["source_set_id"])
    op.create_index("ix_research_runs_workspace_created", "research_runs", ["workspace_id", "created_at"])
    op.create_table("run_artifacts",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("research_run_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("research_run_id", "ordinal", name="uq_run_artifacts_run_ordinal"))
    op.create_index("ix_run_artifacts_research_run_id", "run_artifacts", ["research_run_id"])
    op.create_index("ix_run_artifacts_run_created", "run_artifacts", ["research_run_id", "created_at"])

def downgrade() -> None:
    op.drop_index("ix_run_artifacts_run_created", table_name="run_artifacts")
    op.drop_index("ix_run_artifacts_research_run_id", table_name="run_artifacts")
    op.drop_table("run_artifacts")
    for name in ("ix_research_runs_workspace_created", "ix_research_runs_source_set_id", "ix_research_runs_research_question_id", "ix_research_runs_created_by", "ix_research_runs_workspace_id"):
        op.drop_index(name, table_name="research_runs")
    op.drop_table("research_runs")
