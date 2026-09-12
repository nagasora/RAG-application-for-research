"""Persist partial experiment-analysis failures with saved comparisons.

Revision ID: 20260728_0031
Revises: 20260727_0030
"""
from alembic import op
import sqlalchemy as sa


revision = "20260728_0031"
down_revision = "20260727_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("saved_comparisons") as batch:
        batch.add_column(
            sa.Column("analysis_errors", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    bind = op.get_bind()
    # JSON renders as text on SQLite and PostgreSQL for this cast.  Refuse to
    # lose any recorded partial failure rather than silently dropping it.
    count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM saved_comparisons "
        "WHERE analysis_errors IS NOT NULL "
        "AND CAST(analysis_errors AS TEXT) NOT IN ('[]', '{}', 'null', 'NULL')"
    )).scalar_one()
    if count:
        raise RuntimeError(
            "cannot downgrade 20260728_0031: saved_comparisons.analysis_errors="
            f"{count}. Export or explicitly resolve these partial failures before downgrade."
        )
    with op.batch_alter_table("saved_comparisons") as batch:
        batch.drop_column("analysis_errors")
