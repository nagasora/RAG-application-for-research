"""Add vector embeddings and persistent research conversations."""

from alembic import op
import sqlalchemy as sa

revision = "20260713_0005"
down_revision = "20260712_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.String(36), sa.ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "research_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_conversations_workspace_id", "research_conversations", ["workspace_id"])
    op.create_index("ix_research_conversations_created_by", "research_conversations", ["created_by"])
    op.create_table(
        "research_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("research_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_research_messages_role"),
    )
    op.create_index("ix_research_messages_conversation_id", "research_messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("research_messages")
    op.drop_table("research_conversations")
    op.drop_table("chunk_embeddings")
