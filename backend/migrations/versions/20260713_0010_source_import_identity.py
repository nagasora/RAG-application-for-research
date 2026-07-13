"""Make parsed-source identity include kind and locator.

Revision ID: 20260713_0010
Revises: 20260713_0009
"""

from alembic import op
import sqlalchemy as sa


revision = "20260713_0010"
down_revision = "20260713_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_versions") as batch_op:
        batch_op.drop_constraint("uq_source_versions_workspace_content_hash", type_="unique")
        batch_op.create_unique_constraint(
            "uq_source_versions_workspace_kind_locator_content_hash",
            ["workspace_id", "kind", "locator", "content_hash"],
        )


def downgrade() -> None:
    # Revision 0010 intentionally broadens source identity: the same bytes may
    # be imported under different locators or parsers.  Revision 0009 cannot
    # represent that state.  Fail before changing the schema rather than
    # silently discarding provenance or leaving a failed DDL operation to
    # report an opaque duplicate-key error.
    bind = op.get_bind()
    conflicts = bind.execute(sa.text("""
        SELECT workspace_id, content_hash, COUNT(*) AS version_count
        FROM source_versions
        GROUP BY workspace_id, content_hash
        HAVING COUNT(*) > 1
        ORDER BY workspace_id, content_hash
        LIMIT 5
    """)).mappings().all()
    if conflicts:
        examples = ", ".join(
            f"workspace={row['workspace_id']}, hash={row['content_hash']}, versions={row['version_count']}"
            for row in conflicts
        )
        raise RuntimeError(
            "Cannot downgrade 20260713_0010_source_import_identity: "
            "source_versions contains multiple kind/locator versions for the same "
            f"workspace and content hash ({examples}). Resolve those provenance "
            "records explicitly before downgrading; no records were changed."
        )
    with op.batch_alter_table("source_versions") as batch_op:
        batch_op.drop_constraint("uq_source_versions_workspace_kind_locator_content_hash", type_="unique")
        batch_op.create_unique_constraint(
            "uq_source_versions_workspace_content_hash",
            ["workspace_id", "content_hash"],
        )
