"""Persist abstract-only external imports and discovery search context.

Revision ID: 20260727_0029
Revises: 20260721_0028
"""

from alembic import op
import sqlalchemy as sa
from uuid import uuid4


revision = "20260727_0029"
down_revision = "20260721_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("papers") as batch:
        batch.add_column(sa.Column("content_scope", sa.String(32), nullable=False, server_default="full_text"))
    with op.batch_alter_table("discovery_items") as batch:
        batch.add_column(sa.Column("paper_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("search_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.create_foreign_key("fk_discovery_items_paper_id", "papers", ["paper_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_discovery_items_paper_id", "discovery_items", ["paper_id"])
    op.create_table(
        "paper_external_identifiers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("paper_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("identifier", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "provider", "identifier", name="uq_paper_external_identifier_workspace"),
    )
    op.create_index("ix_paper_external_identifiers_workspace_id", "paper_external_identifiers", ["workspace_id"])
    op.create_index("ix_paper_external_identifiers_paper", "paper_external_identifiers", ["paper_id"])
    # Legacy metadata-only imports have no original asset.  Mark their scope
    # explicitly and retain the existing external ID as an identity row.
    op.execute(sa.text(
        "UPDATE papers SET content_scope = 'abstract_only' "
        "WHERE storage_key IS NULL AND lower(source) IN ('arxiv', 'doi', 'semantic scholar')"
    ))
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, workspace_id, source, external_id, created_at FROM papers "
        "WHERE external_id IS NOT NULL AND trim(external_id) <> '' "
        "AND lower(source) IN ('arxiv', 'doi', 'semantic scholar')"
    )).mappings()
    seen: set[tuple[str, str, str]] = set()
    values: list[dict] = []
    for row in rows:
        source = str(row["source"] or "").casefold()
        raw_identifier = str(row["external_id"] or "").strip()
        if source == "arxiv":
            provider = "arxiv"
            identifier = raw_identifier.removeprefix("arXiv:").removeprefix("arxiv:")
            identifier = identifier.replace("https://arxiv.org/abs/", "").replace("http://arxiv.org/abs/", "")
            identifier = identifier.split("?")[0].strip()
            if "v" in identifier.rsplit("/", 1)[-1]:
                base, marker, version = identifier.rpartition("v")
                if marker and version.isdigit():
                    identifier = base
        else:
            provider = "semantic_scholar" if source == "semantic scholar" else "doi"
            identifier = raw_identifier
            if provider == "doi":
                lowered = identifier.casefold()
                for prefix in ("doi:", "https://doi.org/", "http://doi.org/"):
                    if lowered.startswith(prefix):
                        identifier = identifier[len(prefix):]
                        break
        identifier = identifier.strip().casefold().rstrip("/")
        key = (str(row["workspace_id"]), provider, identifier)
        if not identifier or key in seen:
            continue
        seen.add(key)
        values.append({
            "id": str(uuid4()), "workspace_id": row["workspace_id"], "paper_id": row["id"],
            "provider": provider, "identifier": identifier, "created_at": row["created_at"],
        })
    if values:
        bind.execute(sa.table(
            "paper_external_identifiers",
            sa.column("id"), sa.column("workspace_id"), sa.column("paper_id"),
            sa.column("provider"), sa.column("identifier"), sa.column("created_at"),
        ).insert(), values)


def downgrade() -> None:
    op.drop_index("ix_paper_external_identifiers_paper", table_name="paper_external_identifiers")
    op.drop_index("ix_paper_external_identifiers_workspace_id", table_name="paper_external_identifiers")
    op.drop_table("paper_external_identifiers")
    op.drop_index("ix_discovery_items_paper_id", table_name="discovery_items")
    with op.batch_alter_table("discovery_items") as batch:
        batch.drop_constraint("fk_discovery_items_paper_id", type_="foreignkey")
        batch.drop_column("search_context")
        batch.drop_column("paper_id")
    with op.batch_alter_table("papers") as batch:
        batch.drop_column("content_scope")
