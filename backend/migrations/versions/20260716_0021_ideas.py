"""Idea inbox."""
from alembic import op
import sqlalchemy as sa
revision="20260716_0021"; down_revision="20260716_0020"; branch_labels=None; depends_on=None
def upgrade():
 op.create_table("ideas",sa.Column("id",sa.String(36),primary_key=True),sa.Column("workspace_id",sa.String(36),nullable=False),sa.Column("created_by",sa.String(36)),sa.Column("kind",sa.String(24),nullable=False),sa.Column("content",sa.Text,nullable=False),sa.Column("research_run_id",sa.String(36)),sa.Column("claim_id",sa.String(128)),sa.Column("paper_id",sa.String(36)),sa.Column("source_span_id",sa.String(36)),sa.Column("checklist",sa.JSON,nullable=False),sa.Column("status",sa.String(24),nullable=False),sa.Column("hypothesis_card_id",sa.String(36)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="CASCADE"));op.create_index("ix_ideas_workspace_id","ideas",["workspace_id"])
def downgrade(): op.drop_index("ix_ideas_workspace_id",table_name="ideas");op.drop_table("ideas")
