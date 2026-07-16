"""Append-only belief ledger. Revision ID: 20260716_0020. Revises: 20260716_0019."""
from alembic import op
import sqlalchemy as sa
revision="20260716_0020"; down_revision="20260716_0019"; branch_labels=None; depends_on=None
def upgrade():
 op.create_table("belief_events",sa.Column("id",sa.String(36),primary_key=True),sa.Column("workspace_id",sa.String(36),nullable=False),sa.Column("created_by",sa.String(36)),sa.Column("belief_key",sa.String(128),nullable=False),sa.Column("content",sa.Text,nullable=False),sa.Column("status",sa.String(24),nullable=False),sa.Column("reason",sa.Text,nullable=False,server_default=""),sa.Column("hypothesis_card_id",sa.String(36)),sa.Column("reasoning_run_id",sa.String(36)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["created_by"],["users.id"],ondelete="SET NULL")); op.create_index("ix_belief_events_workspace_belief_created","belief_events",["workspace_id","belief_key","created_at"])
def downgrade(): op.drop_index("ix_belief_events_workspace_belief_created",table_name="belief_events");op.drop_table("belief_events")
