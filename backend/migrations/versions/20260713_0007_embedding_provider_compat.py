"""Forward-compatible embedding provider metadata and ready-paper backfill."""

from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260713_0007"
down_revision = "20260713_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("embedding_jobs")}
    if "provider" not in columns:
        op.add_column("embedding_jobs", sa.Column("provider", sa.String(32), nullable=True))
        op.execute("UPDATE embedding_jobs SET provider = 'openai' WHERE provider IS NULL")
        op.alter_column("embedding_jobs", "provider", existing_type=sa.String(32), nullable=False)

    inspector = sa.inspect(bind)
    unique_constraints = {item.get("name"): item for item in inspector.get_unique_constraints("embedding_jobs")}
    old_name = "uq_embedding_jobs_paper_model"
    if old_name in unique_constraints:
        op.drop_constraint(old_name, "embedding_jobs", type_="unique")
    if "uq_embedding_jobs_paper_provider_model" not in unique_constraints:
        op.create_unique_constraint(
            "uq_embedding_jobs_paper_provider_model",
            "embedding_jobs",
            ["paper_id", "provider", "model"],
        )

    # This also repairs databases where the original 0006 revision was applied
    # before its ready-paper backfill existed.
    ready = bind.execute(sa.text(
        "SELECT p.id AS paper_id, p.workspace_id AS workspace_id, COUNT(c.id) AS total_chunks "
        "FROM papers p JOIN chunks c ON c.paper_id = p.id "
        "WHERE p.status = 'ready' AND NOT EXISTS ("
        "SELECT 1 FROM embedding_jobs e WHERE e.paper_id = p.id "
        "AND e.provider = 'openai' AND e.model = 'text-embedding-3-small') "
        "GROUP BY p.id, p.workspace_id"
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
    # Preserve provider metadata on downgrade; removing it would make jobs
    # ambiguous when multiple embedding backends use the same model name.
    pass
