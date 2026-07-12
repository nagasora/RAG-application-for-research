"""Initial papers and chunks schema, including original-file metadata."""

from alembic import op
import sqlalchemy as sa

revision = "20260712_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_papers_storage_key"),
        sa.UniqueConstraint("user_id", "content_hash", name="uq_papers_user_content_hash"),
    )
    op.create_index("ix_papers_user_id", "papers", ["user_id"])
    op.create_index("ix_papers_status", "papers", ["status"])
    op.create_table(
        "chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chunks_paper_id", "chunks", ["paper_id"])


def downgrade() -> None:
    op.drop_index("ix_chunks_paper_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_papers_status", table_name="papers")
    op.drop_index("ix_papers_user_id", table_name="papers")
    op.drop_table("papers")
