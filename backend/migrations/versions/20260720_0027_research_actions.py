"""Add provenance-preserving research actions.

Revision ID: 20260720_0027
Revises: 20260719_0026
"""
from alembic import op
import sqlalchemy as sa


revision = "20260720_0027"
down_revision = "20260719_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36)),
        sa.Column("idea_id", sa.String(36)),
        sa.Column("research_run_id", sa.String(36)),
        sa.Column("claim_id", sa.String(128)),
        sa.Column("claim_snapshot", sa.JSON(none_as_null=True)),
        sa.Column("source_span_id", sa.String(36)),
        sa.Column("evidence_ref_id", sa.String(36)),
        sa.Column("origin_node_id", sa.String(36)),
        sa.Column("experiment_plan_id", sa.String(36)),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("due_date", sa.String(10)),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("generation_class", sa.String(24), nullable=False, server_default="unverified"),
        sa.Column("generation_metadata", sa.JSON, nullable=False),
        sa.Column("human_decision", sa.String(24), nullable=False, server_default="unreviewed"),
        sa.Column("human_reason", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_span_id"], ["source_spans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evidence_ref_id"], ["evidence_refs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["origin_node_id"], ["knowledge_nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["experiment_plan_id"], ["experiment_plans.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('open','in_progress','done','cancelled')", name="ck_research_actions_status"),
        sa.CheckConstraint("generation_class IN ('hypothesis','inference','unverified')", name="ck_research_actions_generation_class"),
        sa.CheckConstraint("human_decision IN ('unreviewed','accepted','held','rejected')", name="ck_research_actions_human_decision"),
    )
    op.create_index("ix_research_actions_workspace_status_due", "research_actions", ["workspace_id", "status", "due_date"])
    op.create_index("ix_research_actions_idea", "research_actions", ["idea_id"])
    op.create_index("ix_research_actions_node", "research_actions", ["origin_node_id"])
    op.create_index("ix_research_actions_experiment", "research_actions", ["experiment_plan_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM research_actions")).scalar_one():
        raise RuntimeError("Cannot downgrade 20260720_0027_research_actions while research action data exists.")
    op.drop_table("research_actions")
