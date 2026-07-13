"""Add durable asynchronous embedding jobs."""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
from uuid import uuid4


revision = "20260713_0006"
down_revision = "20260713_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("completed_chunks", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_embedding_jobs_status",
        ),
        sa.UniqueConstraint("paper_id", "provider", "model", name="uq_embedding_jobs_paper_provider_model"),
    )
    op.create_index("ix_embedding_jobs_workspace_id", "embedding_jobs", ["workspace_id"])
    op.create_index("ix_embedding_jobs_paper_id", "embedding_jobs", ["paper_id"])
    op.create_index("ix_embedding_jobs_status", "embedding_jobs", ["status"])

    # Existing ready papers must not stay permanently lexical-only after rollout.
    bind = op.get_bind()
    ready = bind.execute(sa.text(
        "SELECT p.id AS paper_id, p.workspace_id AS workspace_id, COUNT(c.id) AS total_chunks "
        "FROM papers p JOIN chunks c ON c.paper_id = p.id "
        "WHERE p.status = 'ready' GROUP BY p.id, p.workspace_id"
    )).mappings().all()
    if ready:
        now = datetime.now(timezone.utc)
        jobs = sa.table(
            "embedding_jobs",
            sa.column("id", sa.String), sa.column("workspace_id", sa.String),
            sa.column("paper_id", sa.String), sa.column("provider", sa.String),
            sa.column("model", sa.String), sa.column("status", sa.String),
            sa.column("progress", sa.Integer), sa.column("attempts", sa.Integer),
            sa.column("total_chunks", sa.Integer), sa.column("completed_chunks", sa.Integer),
            sa.column("error_code", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(jobs, [{
            "id": str(uuid4()), "workspace_id": row["workspace_id"],
            "paper_id": row["paper_id"], "provider": "openai",
            "model": "text-embedding-3-small", "status": "queued",
            "progress": 0, "attempts": 0, "total_chunks": int(row["total_chunks"]),
            "completed_chunks": 0, "error_code": None,
            "created_at": now, "updated_at": now,
        } for row in ready])


def downgrade() -> None:
    op.drop_table("embedding_jobs")
