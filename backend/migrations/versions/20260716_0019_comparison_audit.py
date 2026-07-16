"""Persist comparison source and review snapshots."""
from alembic import op
import sqlalchemy as sa
revision = "20260716_0019"
down_revision = "20260716_0018"
branch_labels = None
depends_on = None
def upgrade() -> None:
    with op.batch_alter_table("saved_comparisons") as batch:
        batch.add_column(sa.Column("source_set_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("citation_snapshot", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("human_judgment", sa.String(16), nullable=False, server_default="unreviewed"))
        batch.add_column(sa.Column("judgment_reason", sa.Text(), nullable=False, server_default=""))
        batch.create_foreign_key("fk_saved_comparisons_source_set", "source_sets", ["source_set_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_saved_comparisons_source_set_id", ["source_set_id"])
def downgrade() -> None:
    with op.batch_alter_table("saved_comparisons") as batch:
        batch.drop_index("ix_saved_comparisons_source_set_id")
        batch.drop_constraint("fk_saved_comparisons_source_set", type_="foreignkey")
        batch.drop_column("judgment_reason"); batch.drop_column("human_judgment"); batch.drop_column("citation_snapshot"); batch.drop_column("source_set_id")
