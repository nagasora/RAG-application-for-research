"""Add immutable source provenance and the research knowledge graph.

Source versions are content-addressed.  Graph nodes and edges only refer to
immutable spans through evidence_refs, so generated ideas never overwrite the
material that grounded them.
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
from uuid import uuid4


revision = "20260713_0009"
down_revision = "20260713_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "content_hash", name="uq_source_versions_workspace_content_hash"),
    )
    op.create_index("ix_source_versions_workspace_id", "source_versions", ["workspace_id"])
    op.create_index("ix_source_versions_paper_id", "source_versions", ["paper_id"])
    op.create_index("ix_source_versions_workspace_kind_created", "source_versions", ["workspace_id", "kind", "created_at"])

    op.create_table(
        "source_spans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("cell", sa.JSON(), nullable=True),
        sa.Column("locator_json", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["source_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_spans_workspace_id", "source_spans", ["workspace_id"])
    op.create_index("ix_source_spans_source_version_id", "source_spans", ["source_version_id"])
    op.create_index("ix_source_spans_source_version_page", "source_spans", ["source_version_id", "page"])

    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="review_pending"),
        sa.Column("layer", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False, server_default="unclassified"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("node_type IN ('source', 'idea', 'constraint', 'hypothesis')", name="ck_knowledge_nodes_type"),
        sa.CheckConstraint("status IN ('review_pending', 'active', 'verified', 'rejected', 'superseded', 'review_required', 'pruned')", name="ck_knowledge_nodes_status"),
        sa.CheckConstraint("layer >= 0", name="ck_knowledge_nodes_layer"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_knowledge_nodes_confidence"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_nodes_workspace_id", "knowledge_nodes", ["workspace_id"])
    op.create_index("ix_knowledge_nodes_created_by", "knowledge_nodes", ["created_by"])
    op.create_index("ix_knowledge_nodes_workspace_layer_status", "knowledge_nodes", ["workspace_id", "layer", "status"])
    op.create_index("ix_knowledge_nodes_workspace_phase", "knowledge_nodes", ["workspace_id", "phase"])

    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("source_node_id", sa.String(length=36), nullable=False),
        sa.Column("target_node_id", sa.String(length=36), nullable=False),
        sa.Column("relation", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_node_id != target_node_id", name="ck_knowledge_edges_distinct_nodes"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_node_id"], ["knowledge_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_node_id"], ["knowledge_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_edges_workspace_id", "knowledge_edges", ["workspace_id"])
    op.create_index("ix_knowledge_edges_source_node_id", "knowledge_edges", ["source_node_id"])
    op.create_index("ix_knowledge_edges_target_node_id", "knowledge_edges", ["target_node_id"])
    op.create_index("ix_knowledge_edges_workspace_source", "knowledge_edges", ["workspace_id", "source_node_id"])
    op.create_index("ix_knowledge_edges_workspace_target", "knowledge_edges", ["workspace_id", "target_node_id"])

    op.create_table(
        "evidence_refs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("source_span_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=True),
        sa.Column("knowledge_edge_id", sa.String(length=36), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(knowledge_node_id IS NOT NULL AND knowledge_edge_id IS NULL) OR (knowledge_node_id IS NULL AND knowledge_edge_id IS NOT NULL)", name="ck_evidence_refs_one_subject"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_span_id"], ["source_spans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["knowledge_node_id"], ["knowledge_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_edge_id"], ["knowledge_edges.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_refs_workspace_id", "evidence_refs", ["workspace_id"])
    op.create_index("ix_evidence_refs_source_span_id", "evidence_refs", ["source_span_id"])
    op.create_index("ix_evidence_refs_node", "evidence_refs", ["knowledge_node_id"])
    op.create_index("ix_evidence_refs_edge", "evidence_refs", ["knowledge_edge_id"])

    op.create_table(
        "reasoning_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("operator", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_reasoning_runs_status"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reasoning_runs_workspace_id", "reasoning_runs", ["workspace_id"])
    op.create_index("ix_reasoning_runs_created_by", "reasoning_runs", ["created_by"])
    op.create_index("ix_reasoning_runs_workspace_created", "reasoning_runs", ["workspace_id", "created_at"])

    for name, output in (("reasoning_run_inputs", False), ("reasoning_run_outputs", True)):
        op.create_table(
            name,
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("reasoning_run_id", sa.String(length=36), nullable=False),
            sa.Column("knowledge_node_id", sa.String(length=36), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["reasoning_run_id"], ["reasoning_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["knowledge_node_id"], ["knowledge_nodes.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("reasoning_run_id", "knowledge_node_id", name=f"uq_{name}_run_node"),
        )
        op.create_index(f"ix_{name}_reasoning_run_id", name, ["reasoning_run_id"])
        op.create_index(f"ix_{name}_knowledge_node_id", name, ["knowledge_node_id"])

    op.create_table(
        "node_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("verdict IN ('helpful', 'not_helpful', 'accepted', 'rejected')", name="ck_node_feedback_verdict"),
        sa.CheckConstraint("rating IS NULL OR (rating >= -1 AND rating <= 1)", name="ck_node_feedback_rating"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_node_id"], ["knowledge_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_node_id", "user_id", name="uq_node_feedback_node_user"),
    )
    op.create_index("ix_node_feedback_workspace_id", "node_feedback", ["workspace_id"])
    op.create_index("ix_node_feedback_knowledge_node_id", "node_feedback", ["knowledge_node_id"])
    op.create_index("ix_node_feedback_user_id", "node_feedback", ["user_id"])

    op.create_table(
        "canvas_layouts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("canvas_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=False),
        sa.Column("x", sa.Float(), nullable=False), sa.Column("y", sa.Float(), nullable=False),
        sa.Column("width", sa.Float(), nullable=True), sa.Column("height", sa.Float(), nullable=True),
        sa.Column("z_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collapsed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_node_id"], ["knowledge_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "canvas_id", "knowledge_node_id", name="uq_canvas_layouts_canvas_node"),
    )
    op.create_index("ix_canvas_layouts_workspace_id", "canvas_layouts", ["workspace_id"])
    op.create_index("ix_canvas_layouts_knowledge_node_id", "canvas_layouts", ["knowledge_node_id"])
    op.create_index("ix_canvas_layouts_workspace_canvas", "canvas_layouts", ["workspace_id", "canvas_id"])

    # Existing uploaded papers already have immutable originals and extracted
    # page text. Seed graph anchors now so users do not need to re-ingest their
    # library before they can make grounded source nodes.
    bind = op.get_bind()
    papers = sa.table(
        "papers", sa.column("id"), sa.column("workspace_id"), sa.column("content_hash"),
        sa.column("storage_key"), sa.column("mime_type"), sa.column("title"),
    )
    source_versions = sa.table(
        "source_versions", sa.column("id"), sa.column("workspace_id"),
        sa.column("paper_id"), sa.column("kind"), sa.column("locator"),
        sa.column("content_hash"), sa.column("metadata_json", sa.JSON()), sa.column("created_at"),
    )
    source_spans = sa.table(
        "source_spans", sa.column("id"), sa.column("workspace_id"),
        sa.column("source_version_id"), sa.column("page"), sa.column("line_start"),
        sa.column("line_end"), sa.column("char_start"), sa.column("char_end"),
        sa.column("bbox", sa.JSON()), sa.column("cell", sa.JSON()), sa.column("locator_json", sa.JSON()),
        sa.column("text"), sa.column("created_at"),
    )
    paper_pages = sa.table(
        "paper_pages", sa.column("paper_id"), sa.column("page"),
        sa.column("text"), sa.column("text_source"),
    )
    for paper in bind.execute(sa.select(papers)).mappings():
        if not paper["content_hash"]:
            continue
        existing = bind.execute(sa.select(source_versions.c.id).where(
            source_versions.c.workspace_id == paper["workspace_id"],
            source_versions.c.content_hash == paper["content_hash"],
        ).limit(1)).scalar_one_or_none()
        if existing is not None:
            continue
        source_id, now = str(uuid4()), datetime.now(timezone.utc)
        bind.execute(sa.insert(source_versions).values(
            id=source_id, workspace_id=paper["workspace_id"], paper_id=paper["id"],
            kind="paper", locator=paper["storage_key"] or f"paper:{paper['id']}",
            content_hash=paper["content_hash"],
            metadata_json={"mime_type": paper["mime_type"], "title": paper["title"]},
            created_at=now,
        ))
        pages = bind.execute(sa.select(paper_pages).where(
            paper_pages.c.paper_id == paper["id"]
        )).mappings()
        for page in pages:
            if not page["text"]:
                continue
            bind.execute(sa.insert(source_spans).values(
                id=str(uuid4()), workspace_id=paper["workspace_id"], source_version_id=source_id,
                page=page["page"], line_start=None, line_end=None, char_start=None, char_end=None,
                bbox=None, cell=None,
                locator_json={"paper_id": paper["id"], "text_source": page["text_source"]},
                text=page["text"], created_at=now,
            ))


def downgrade() -> None:
    for table in ("canvas_layouts", "node_feedback", "reasoning_run_outputs", "reasoning_run_inputs", "reasoning_runs", "evidence_refs", "knowledge_edges", "knowledge_nodes", "source_spans", "source_versions"):
        op.drop_table(table)
