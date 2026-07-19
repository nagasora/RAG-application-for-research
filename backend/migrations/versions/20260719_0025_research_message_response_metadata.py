"""Persist immutable assistant-response metadata for research conversations.

Revision ID: 20260719_0025
Revises: 20260716_0024
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_0025"
down_revision = "20260716_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A server default backfills legacy turns safely. New user turns explicitly
    # write {}, while assistant turns carry the normalized response contract.
    with op.batch_alter_table("research_messages") as batch:
        batch.add_column(
            sa.Column(
                "response_metadata", sa.JSON(), nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        count = bind.execute(sa.text(
            "SELECT COUNT(*) FROM research_messages "
            "WHERE CAST(response_metadata AS TEXT) <> '{}'"
        )).scalar_one()
    else:
        count = bind.execute(sa.text(
            "SELECT COUNT(*) FROM research_messages WHERE response_metadata <> '{}'"
        )).scalar_one()
    if count:
        raise RuntimeError(
            "Cannot downgrade 20260719_0025 while immutable assistant response metadata exists."
        )
    with op.batch_alter_table("research_messages") as batch:
        batch.drop_column("response_metadata")
