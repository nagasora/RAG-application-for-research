"""Experiment plans. Revision ID: 20260716_0022. Revises: 20260716_0021."""
from alembic import op
import sqlalchemy as sa
revision="20260716_0022"; down_revision="20260716_0021"; branch_labels=None; depends_on=None
def upgrade():
 op.create_table("experiment_plans",sa.Column("id",sa.String(36),primary_key=True),sa.Column("workspace_id",sa.String(36),nullable=False),sa.Column("created_by",sa.String(36)),sa.Column("hypothesis_card_id",sa.String(36)),sa.Column("plan",sa.JSON,nullable=False),sa.Column("results",sa.JSON,nullable=False),sa.Column("history",sa.JSON,nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["created_by"],["users.id"],ondelete="SET NULL"))
def downgrade(): op.drop_table("experiment_plans")
