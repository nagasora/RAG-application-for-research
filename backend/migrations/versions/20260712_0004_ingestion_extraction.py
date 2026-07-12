"""Add ingestion jobs, page text provenance, and document elements."""

from alembic import op
import sqlalchemy as sa

revision = "20260712_0004"
down_revision = "20260712_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("ingestion_jobs", sa.Column("id", sa.String(36), primary_key=True), sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("progress", sa.Integer(), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("error_message", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("paper_id", name="uq_ingestion_jobs_paper_id"))
    op.create_index("ix_ingestion_jobs_workspace_id", "ingestion_jobs", ["workspace_id"]); op.create_index("ix_ingestion_jobs_paper_id", "ingestion_jobs", ["paper_id"], unique=True); op.create_index("ix_ingestion_jobs_status", "ingestion_jobs", ["status"])
    op.create_table("paper_pages", sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True), sa.Column("page", sa.Integer(), primary_key=True), sa.Column("text", sa.Text(), nullable=False), sa.Column("text_source", sa.String(16), nullable=False), sa.Column("quality", sa.Float(), nullable=False))
    op.create_table("document_elements", sa.Column("id", sa.String(36), primary_key=True), sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False), sa.Column("page", sa.Integer(), nullable=False), sa.Column("kind", sa.String(16), nullable=False), sa.Column("bbox", sa.JSON()), sa.Column("text", sa.Text(), nullable=False), sa.Column("structured_data", sa.JSON()), sa.Column("asset_key", sa.Text()), sa.UniqueConstraint("asset_key", name="uq_document_elements_asset_key"))
    op.create_index("ix_document_elements_paper_id", "document_elements", ["paper_id"]); op.create_index("ix_document_elements_page", "document_elements", ["page"]); op.create_index("ix_document_elements_kind", "document_elements", ["kind"])


def downgrade() -> None:
    op.drop_table("document_elements")
    op.drop_table("paper_pages")
    op.drop_table("ingestion_jobs")
