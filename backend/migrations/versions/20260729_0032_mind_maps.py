"""Add workspace-scoped, evidence-backed mind maps.

Revision ID: 20260729_0032
Revises: 20260728_0031
"""
from alembic import op
import sqlalchemy as sa


revision = "20260729_0032"
down_revision = "20260728_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mind_maps",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("source_scope", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("generation_run_id", sa.String(length=36), sa.ForeignKey("research_runs.id", ondelete="SET NULL")),
        sa.Column("generation_kind", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "generation_run_id", name="uq_mind_maps_generation_confirmation"),
    )
    op.create_index("ix_mind_maps_workspace_created", "mind_maps", ["workspace_id", "created_at"])
    op.create_table(
        "mind_map_nodes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("mind_map_id", sa.String(length=36), sa.ForeignKey("mind_maps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.String(length=36), sa.ForeignKey("mind_map_nodes.id", ondelete="CASCADE")),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="review_pending"),
        sa.Column("generation_run_id", sa.String(length=36), sa.ForeignKey("research_runs.id", ondelete="SET NULL")),
        sa.Column("confirmation_client_id", sa.String(length=128)),
        sa.Column("knowledge_node_id", sa.String(length=36), sa.ForeignKey("knowledge_nodes.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('root', 'theme', 'claim', 'question', 'method', 'finding', 'task', 'note', 'link')", name="ck_mind_map_nodes_kind"),
        sa.CheckConstraint("status IN ('review_pending', 'active', 'rejected')", name="ck_mind_map_nodes_status"),
        sa.UniqueConstraint("mind_map_id", "parent_id", "sort_order", name="uq_mind_map_nodes_parent_order"),
        sa.UniqueConstraint("parent_id", "confirmation_client_id", name="uq_mind_map_nodes_confirmation"),
    )
    op.create_index("ix_mind_map_nodes_map_parent", "mind_map_nodes", ["mind_map_id", "parent_id", "sort_order"])
    op.create_table(
        "mind_map_node_evidence",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("mind_map_node_id", sa.String(length=36), sa.ForeignKey("mind_map_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_ref_id", sa.String(length=36), sa.ForeignKey("evidence_refs.id", ondelete="RESTRICT")),
        sa.Column("source_span_id", sa.String(length=36), sa.ForeignKey("source_spans.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(evidence_ref_id IS NOT NULL AND source_span_id IS NULL) OR (evidence_ref_id IS NULL AND source_span_id IS NOT NULL)", name="ck_mind_map_node_evidence_one_anchor"),
        sa.UniqueConstraint("mind_map_node_id", "evidence_ref_id", name="uq_mind_map_evidence_ref"),
        sa.UniqueConstraint("mind_map_node_id", "source_span_id", name="uq_mind_map_source_span"),
    )
    with op.batch_alter_table("notes") as batch:
        batch.add_column(sa.Column(
            "mind_map_node_id",
            sa.String(length=36),
            sa.ForeignKey("mind_map_nodes.id", name="fk_notes_mind_map_node", ondelete="SET NULL"),
        ))
        batch.add_column(sa.Column("origin_snapshot", sa.JSON(), nullable=True))
    with op.batch_alter_table("research_actions") as batch:
        batch.add_column(sa.Column(
            "mind_map_node_id",
            sa.String(length=36),
            sa.ForeignKey("mind_map_nodes.id", name="fk_research_actions_mind_map_node", ondelete="SET NULL"),
        ))
        batch.add_column(sa.Column("origin_kind", sa.String(length=32), nullable=True))
        batch.create_unique_constraint(
            "uq_research_actions_mind_map_node_extraction",
            ["workspace_id", "mind_map_node_id", "extraction_source", "extraction_ordinal"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM mind_maps")).scalar_one():
        raise RuntimeError("cannot downgrade 20260729_0032 while mind maps exist")
    if bind.execute(sa.text(
        "SELECT COUNT(*) FROM notes WHERE mind_map_node_id IS NOT NULL OR origin_snapshot IS NOT NULL"
    )).scalar_one():
        raise RuntimeError("cannot downgrade 20260729_0032 while mind-map Note provenance exists")
    if bind.execute(sa.text(
        "SELECT COUNT(*) FROM research_actions WHERE mind_map_node_id IS NOT NULL OR origin_kind = 'mind_map'"
    )).scalar_one():
        raise RuntimeError("cannot downgrade 20260729_0032 while mind-map Action provenance exists")
    with op.batch_alter_table("research_actions") as batch:
        batch.drop_constraint("uq_research_actions_mind_map_node_extraction", type_="unique")
        batch.drop_column("origin_kind")
        batch.drop_column("mind_map_node_id")
    with op.batch_alter_table("notes") as batch:
        batch.drop_column("origin_snapshot")
        batch.drop_column("mind_map_node_id")
    op.drop_table("mind_map_node_evidence")
    op.drop_index("ix_mind_map_nodes_map_parent", table_name="mind_map_nodes")
    op.drop_table("mind_map_nodes")
    op.drop_index("ix_mind_maps_workspace_created", table_name="mind_maps")
    op.drop_table("mind_maps")
