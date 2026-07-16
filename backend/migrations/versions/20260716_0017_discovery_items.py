"""Add review-gated discovery snapshots.

Revision ID: 20260716_0017
Revises: 20260716_0016
"""
from alembic import op
import sqlalchemy as sa
revision = "20260716_0017"
down_revision = "20260716_0016"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("discovery_items", sa.Column("id", sa.String(36), primary_key=True), sa.Column("workspace_id", sa.String(36), nullable=False), sa.Column("created_by", sa.String(36)), sa.Column("provider", sa.String(64), nullable=False), sa.Column("provider_paper_id", sa.String(256), nullable=False), sa.Column("classification", sa.String(32), nullable=False), sa.Column("review_status", sa.String(24), nullable=False, server_default="pending"), sa.Column("title", sa.Text, nullable=False), sa.Column("abstract", sa.Text, nullable=False, server_default=""), sa.Column("source_quote", sa.Text, nullable=False, server_default=""), sa.Column("source_url", sa.Text, nullable=False, server_default=""), sa.Column("license", sa.String(128), nullable=False, server_default="unknown"), sa.Column("rate_limit_policy", sa.String(128), nullable=False, server_default=""), sa.Column("snapshot", sa.JSON, nullable=False), sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by"],["users.id"],ondelete="SET NULL"))
    op.create_index("ix_discovery_items_workspace_status_created", "discovery_items", ["workspace_id", "review_status", "created_at"])
def downgrade():
    op.drop_index("ix_discovery_items_workspace_status_created", table_name="discovery_items"); op.drop_table("discovery_items")
