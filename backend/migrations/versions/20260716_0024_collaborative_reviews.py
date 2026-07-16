"""Workspace-scoped collaborative review threads.

Revision ID: 20260716_0024
Revises: 20260716_0023
"""
from alembic import op
import sqlalchemy as sa

revision = "20260716_0024"
down_revision = "20260716_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_threads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36)),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("research_run_id", sa.String(36)),
        sa.Column("claim_id", sa.String(128)),
        sa.Column("claim_artifact_id", sa.String(36)),
        sa.Column("claim_snapshot", sa.JSON(none_as_null=True)),
        sa.Column("evidence_ref_id", sa.String(36)),
        sa.Column("assigned_to", sa.String(36)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["claim_artifact_id"], ["run_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_ref_id"], ["evidence_refs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "(evidence_ref_id IS NOT NULL AND research_run_id IS NULL AND claim_id IS NULL "
            "AND claim_artifact_id IS NULL AND claim_snapshot IS NULL) OR "
            "(evidence_ref_id IS NULL AND research_run_id IS NOT NULL AND claim_id IS NOT NULL "
            "AND claim_artifact_id IS NOT NULL AND claim_snapshot IS NOT NULL)",
            name="ck_review_threads_one_anchor",
        ),
        sa.CheckConstraint("status IN ('open','resolved')", name="ck_review_threads_status"),
    )
    op.create_index("ix_review_threads_workspace_id", "review_threads", ["workspace_id"])
    op.create_index("ix_review_threads_created_by", "review_threads", ["created_by"])
    op.create_index("ix_review_threads_research_run_id", "review_threads", ["research_run_id"])
    op.create_index("ix_review_threads_claim_artifact_id", "review_threads", ["claim_artifact_id"])
    op.create_index("ix_review_threads_evidence_ref_id", "review_threads", ["evidence_ref_id"])
    op.create_index("ix_review_threads_assigned_to", "review_threads", ["assigned_to"])
    op.create_index("ix_review_threads_workspace_status_updated", "review_threads", ["workspace_id", "status", "updated_at"])
    op.create_index("ix_review_threads_assigned", "review_threads", ["assigned_to", "status"])

    op.create_table(
        "review_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_thread_id", sa.String(36), nullable=False),
        sa.Column("author_id", sa.String(36)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_thread_id"], ["review_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_review_comments_author_id", "review_comments", ["author_id"])
    op.create_index("ix_review_comments_thread_created", "review_comments", ["review_thread_id", "created_at"])

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_thread_id", sa.String(36), nullable=False),
        sa.Column("decided_by", sa.String(36)),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_thread_id"], ["review_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "verdict IN ('accepted','rejected','changes_requested','needs_more_evidence')",
            name="ck_review_decisions_verdict",
        ),
    )
    op.create_index("ix_review_decisions_decided_by", "review_decisions", ["decided_by"])
    op.create_index("ix_review_decisions_thread_created", "review_decisions", ["review_thread_id", "created_at"])


def downgrade():
    count = op.get_bind().execute(sa.text(
        "SELECT (SELECT COUNT(*) FROM review_threads) + "
        "(SELECT COUNT(*) FROM review_comments) + (SELECT COUNT(*) FROM review_decisions)"
    )).scalar_one()
    if count:
        raise RuntimeError(
            "Cannot downgrade 20260716_0024_collaborative_reviews while review audit data exists."
        )
    op.drop_index("ix_review_decisions_thread_created", table_name="review_decisions")
    op.drop_index("ix_review_decisions_decided_by", table_name="review_decisions")
    op.drop_table("review_decisions")
    op.drop_index("ix_review_comments_thread_created", table_name="review_comments")
    op.drop_index("ix_review_comments_author_id", table_name="review_comments")
    op.drop_table("review_comments")
    op.drop_index("ix_review_threads_assigned", table_name="review_threads")
    op.drop_index("ix_review_threads_workspace_status_updated", table_name="review_threads")
    op.drop_index("ix_review_threads_assigned_to", table_name="review_threads")
    op.drop_index("ix_review_threads_evidence_ref_id", table_name="review_threads")
    op.drop_index("ix_review_threads_research_run_id", table_name="review_threads")
    op.drop_index("ix_review_threads_claim_artifact_id", table_name="review_threads")
    op.drop_index("ix_review_threads_workspace_id", table_name="review_threads")
    op.drop_index("ix_review_threads_created_by", table_name="review_threads")
    op.drop_table("review_threads")
