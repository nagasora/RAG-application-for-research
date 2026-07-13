"""Add governed relation vocabulary and edge lifecycle audit.

Revision ID: 20260713_0011
Revises: 20260713_0010
"""

from alembic import op
import sqlalchemy as sa


revision = "20260713_0011"
down_revision = "20260713_0010"
branch_labels = None
depends_on = None


RELATIONS = (
    "informs", "supports", "extends", "formulates", "contradicts",
    "implements", "depends_on", "related",
)
STATUSES = (
    "review_pending", "active", "verified", "rejected", "superseded",
    "review_required", "pruned",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    bind = op.get_bind()
    unknown = bind.execute(sa.text(
        f"SELECT relation, COUNT(*) AS edge_count FROM knowledge_edges "
        f"WHERE relation NOT IN ({_quoted(RELATIONS)}) GROUP BY relation LIMIT 5"
    )).mappings().all()
    if unknown:
        examples = ", ".join(
            f"relation={row['relation']}, edges={row['edge_count']}" for row in unknown
        )
        raise RuntimeError(
            "Cannot upgrade 20260713_0011_edge_lifecycle: unknown knowledge edge "
            f"relations exist ({examples}). Rename them to the governed vocabulary first."
        )

    with op.batch_alter_table("knowledge_edges") as batch_op:
        batch_op.add_column(sa.Column("created_by", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("status", sa.String(length=32), server_default="active", nullable=False))
        batch_op.add_column(sa.Column("origin", sa.String(length=32), server_default="manual", nullable=False))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
        batch_op.create_foreign_key(
            "fk_knowledge_edges_created_by_users", "users", ["created_by"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_knowledge_edges_relation", f"relation IN ({_quoted(RELATIONS)})",
        )
        batch_op.create_check_constraint(
            "ck_knowledge_edges_status", f"status IN ({_quoted(STATUSES)})",
        )
        batch_op.create_check_constraint(
            "ck_knowledge_edges_origin", "origin IN ('manual', 'llm', 'import')",
        )
    op.create_index("ix_knowledge_edges_created_by", "knowledge_edges", ["created_by"])

    op.create_table(
        "knowledge_edge_status_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_edge_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_edge_id"], ["knowledge_edges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_edge_status_events_workspace_id", "knowledge_edge_status_events", ["workspace_id"])
    op.create_index("ix_knowledge_edge_status_events_knowledge_edge_id", "knowledge_edge_status_events", ["knowledge_edge_id"])
    op.create_index("ix_knowledge_edge_status_events_actor_id", "knowledge_edge_status_events", ["actor_id"])
    op.create_index("ix_edge_status_events_edge_created", "knowledge_edge_status_events", ["knowledge_edge_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    event_count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM knowledge_edge_status_events"
    )).scalar_one()
    non_default_count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM knowledge_edges "
        "WHERE status != 'active' OR origin != 'manual' OR created_by IS NOT NULL"
    )).scalar_one()
    if event_count or non_default_count:
        raise RuntimeError(
            "Cannot downgrade 20260713_0011_edge_lifecycle: edge lifecycle audit data "
            "or non-default provenance exists. Export and explicitly resolve that immutable "
            "history before downgrading."
        )

    op.drop_table("knowledge_edge_status_events")
    op.drop_index("ix_knowledge_edges_created_by", table_name="knowledge_edges")
    with op.batch_alter_table("knowledge_edges") as batch_op:
        batch_op.drop_constraint("ck_knowledge_edges_origin", type_="check")
        batch_op.drop_constraint("ck_knowledge_edges_status", type_="check")
        batch_op.drop_constraint("ck_knowledge_edges_relation", type_="check")
        batch_op.drop_constraint("fk_knowledge_edges_created_by_users", type_="foreignkey")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("origin")
        batch_op.drop_column("status")
        batch_op.drop_column("created_by")
