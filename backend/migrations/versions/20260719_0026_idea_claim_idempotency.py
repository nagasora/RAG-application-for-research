"""Validate claim-anchored Ideas and make their creates idempotent.

Revision ID: 20260719_0026
Revises: 20260719_0025
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_0026"
down_revision = "20260719_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ideas", recreate="always") as batch:
        batch.add_column(sa.Column("claim_artifact_id", sa.String(36)))
        batch.add_column(sa.Column("claim_snapshot", sa.JSON(none_as_null=True)))
        batch.add_column(sa.Column("idempotency_key", sa.String(64)))
        batch.create_foreign_key(
            "fk_ideas_claim_artifact", "run_artifacts", ["claim_artifact_id"], ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint("uq_ideas_idempotency_key", ["idempotency_key"])


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM ideas WHERE claim_artifact_id IS NOT NULL "
        "OR claim_snapshot IS NOT NULL OR idempotency_key IS NOT NULL"
    )).scalar_one()
    if count:
        raise RuntimeError(
            "Cannot downgrade 20260719_0026 while claim-anchored Idea metadata exists."
        )
    with op.batch_alter_table("ideas", recreate="always") as batch:
        batch.drop_constraint("uq_ideas_idempotency_key", type_="unique")
        batch.drop_constraint("fk_ideas_claim_artifact", type_="foreignkey")
        batch.drop_column("idempotency_key")
        batch.drop_column("claim_snapshot")
        batch.drop_column("claim_artifact_id")
