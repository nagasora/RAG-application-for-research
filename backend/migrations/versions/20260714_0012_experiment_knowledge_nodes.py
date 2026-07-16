"""Allow experiment nodes in the knowledge graph.

Revision ID: 20260714_0012
Revises: 20260713_0011
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_0012"
down_revision = "20260713_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_nodes") as batch_op:
        batch_op.drop_constraint("ck_knowledge_nodes_type", type_="check")
        batch_op.create_check_constraint(
            "ck_knowledge_nodes_type",
            "node_type IN ('source', 'idea', 'constraint', 'hypothesis', 'experiment')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    experiment_count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM knowledge_nodes WHERE node_type = 'experiment'"
    )).scalar_one()
    if experiment_count:
        raise RuntimeError(
            "Cannot downgrade 20260714_0012_experiment_knowledge_nodes while "
            "experiment nodes exist. Export or reclassify them first."
        )
    with op.batch_alter_table("knowledge_nodes") as batch_op:
        batch_op.drop_constraint("ck_knowledge_nodes_type", type_="check")
        batch_op.create_check_constraint(
            "ck_knowledge_nodes_type",
            "node_type IN ('source', 'idea', 'constraint', 'hypothesis')",
        )
