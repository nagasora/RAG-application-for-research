"""Add research workspace assets."""

from alembic import op
import sqlalchemy as sa

revision = "20260712_0003"
down_revision = "20260712_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("tags", sa.Column("id", sa.String(36), primary_key=True), sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(100), nullable=False), sa.Column("color", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("workspace_id", "name", name="uq_tags_workspace_name"))
    op.create_index("ix_tags_workspace_id", "tags", ["workspace_id"])
    op.create_table("paper_tags", sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True), sa.Column("tag_id", sa.String(36), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("notes", sa.Column("id", sa.String(36), primary_key=True), sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=True), sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_notes_workspace_id", "notes", ["workspace_id"]); op.create_index("ix_notes_paper_id", "notes", ["paper_id"])
    op.create_table("search_history", sa.Column("id", sa.String(36), primary_key=True), sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("query", sa.Text(), nullable=False), sa.Column("paper_ids", sa.JSON(), nullable=False), sa.Column("result_summary", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_search_history_workspace_id", "search_history", ["workspace_id"]); op.create_index("ix_search_history_user_id", "search_history", ["user_id"])
    op.create_table("saved_comparisons", sa.Column("id", sa.String(36), primary_key=True), sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("paper_ids", sa.JSON(), nullable=False), sa.Column("result", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_saved_comparisons_workspace_id", "saved_comparisons", ["workspace_id"]); op.create_index("ix_saved_comparisons_user_id", "saved_comparisons", ["user_id"])


def downgrade() -> None:
    op.drop_table("saved_comparisons")
    op.drop_table("search_history")
    op.drop_table("notes")
    op.drop_table("paper_tags")
    op.drop_table("tags")
