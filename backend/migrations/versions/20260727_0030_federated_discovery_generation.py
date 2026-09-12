"""Federated discovery sessions and governed generation settings.

Revision ID: 20260727_0030
Revises: 20260727_0029
"""
from alembic import op
import sqlalchemy as sa

revision = "20260727_0030"
down_revision = "20260727_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_runs") as batch:
        batch.add_column(sa.Column("generation_provider", sa.String(32), nullable=False, server_default=""))
    op.create_table("discovery_search_sessions",
        sa.Column("id",sa.String(36),primary_key=True), sa.Column("workspace_id",sa.String(36),nullable=False), sa.Column("created_by",sa.String(36)),
        sa.Column("criteria",sa.JSON(),nullable=False),sa.Column("query_plan",sa.JSON(),nullable=False),sa.Column("generation_provider",sa.String(32),nullable=False),sa.Column("generation_model",sa.String(128),nullable=False),sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["created_by"],["users.id"],ondelete="SET NULL"))
    op.create_index("ix_discovery_search_sessions_workspace_expires","discovery_search_sessions",["workspace_id","expires_at"])
    op.create_table("discovery_search_candidates",
        sa.Column("id",sa.String(36),primary_key=True),sa.Column("session_id",sa.String(36),nullable=False),sa.Column("canonical_key",sa.String(600),nullable=False),sa.Column("provider",sa.String(64),nullable=False),sa.Column("provider_paper_id",sa.String(256),nullable=False),sa.Column("provider_ids",sa.JSON(),nullable=False),sa.Column("snapshot",sa.JSON(),nullable=False),sa.Column("rank",sa.Integer(),nullable=False),sa.Column("imported_paper_id",sa.String(36)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["session_id"],["discovery_search_sessions.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["imported_paper_id"],["papers.id"],ondelete="SET NULL"),sa.UniqueConstraint("session_id","canonical_key",name="uq_discovery_candidates_session_key"))
    op.create_index("ix_discovery_candidates_session_rank","discovery_search_candidates",["session_id","rank"])
    op.create_table("workspace_generation_settings",sa.Column("workspace_id",sa.String(36),primary_key=True),sa.Column("scopes",sa.JSON(),nullable=False),sa.Column("updated_by",sa.String(36)),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["updated_by"],["users.id"],ondelete="SET NULL"))
    op.create_table("generation_audits",sa.Column("id",sa.String(36),primary_key=True),sa.Column("workspace_id",sa.String(36),nullable=False),sa.Column("created_by",sa.String(36)),sa.Column("scope",sa.String(32),nullable=False),sa.Column("provider",sa.String(32),nullable=False),sa.Column("model",sa.String(128),nullable=False),sa.Column("outcome",sa.String(32),nullable=False),sa.Column("reference_id",sa.String(36)),sa.Column("prompt_version",sa.String(128),nullable=False,server_default=""),sa.Column("usage",sa.JSON(),nullable=False,server_default=sa.text("'{}'")),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["created_by"],["users.id"],ondelete="SET NULL"))
    op.create_index("ix_generation_audits_workspace_created","generation_audits",["workspace_id","created_at"])
    op.create_table("experiment_profiles",sa.Column("id",sa.String(36),primary_key=True),sa.Column("workspace_id",sa.String(36),nullable=False),sa.Column("paper_id",sa.String(36),nullable=False),sa.Column("source_version_id",sa.String(36),nullable=False),sa.Column("content_hash",sa.String(64),nullable=False),sa.Column("provider",sa.String(32),nullable=False),sa.Column("model",sa.String(128),nullable=False),sa.Column("prompt_version",sa.String(128),nullable=False),sa.Column("cache_key",sa.String(128),nullable=False),sa.Column("review_status",sa.String(32),nullable=False),sa.Column("snapshot",sa.JSON(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["paper_id"],["papers.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["source_version_id"],["source_versions.id"],ondelete="RESTRICT"),sa.UniqueConstraint("workspace_id","paper_id","source_version_id","content_hash","provider","model","prompt_version","cache_key",name="uq_experiment_profiles_snapshot"))
    op.create_index("ix_experiment_profiles_workspace_paper","experiment_profiles",["workspace_id","paper_id"])


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in (
            "experiment_profiles", "generation_audits", "workspace_generation_settings",
            "discovery_search_candidates", "discovery_search_sessions",
        )
    }
    counts["research_run_generation"] = bind.execute(sa.text(
        "SELECT COUNT(*) FROM research_runs WHERE generation_provider <> ''"
    )).scalar_one()
    nonempty = ", ".join(f"{name}={count}" for name, count in counts.items() if count)
    if nonempty:
        raise RuntimeError(
            "cannot downgrade 20260727_0030: governed data exists ("
            f"{nonempty}). Export or explicitly resolve this data before downgrade."
        )
    op.drop_index("ix_experiment_profiles_workspace_paper",table_name="experiment_profiles"); op.drop_table("experiment_profiles")
    op.drop_index("ix_generation_audits_workspace_created",table_name="generation_audits"); op.drop_table("generation_audits")
    op.drop_table("workspace_generation_settings")
    op.drop_index("ix_discovery_candidates_session_rank",table_name="discovery_search_candidates"); op.drop_table("discovery_search_candidates")
    op.drop_index("ix_discovery_search_sessions_workspace_expires",table_name="discovery_search_sessions"); op.drop_table("discovery_search_sessions")
    with op.batch_alter_table("research_runs") as batch: batch.drop_column("generation_provider")
