"""Make mind-map task extraction durable and classify mind-map notes.

Revision ID: 20260721_0028
Revises: 20260720_0027
"""

from alembic import op
import sqlalchemy as sa


revision = "20260721_0028"
down_revision = "20260720_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_actions") as batch:
        batch.add_column(sa.Column("extraction_source", sa.String(64)))
        batch.add_column(sa.Column("extraction_ordinal", sa.Integer()))
        batch.create_unique_constraint(
            "uq_research_actions_mind_map_extraction",
            ["workspace_id", "origin_node_id", "extraction_source", "extraction_ordinal"],
        )
    with op.batch_alter_table("notes") as batch:
        batch.add_column(sa.Column("origin_kind", sa.String(32)))
    # Preserve notes produced by the short-lived title-prefix implementation.
    # New reads and writes use origin_kind exclusively.
    op.execute(sa.text(
        "UPDATE notes SET origin_kind = 'mind_map' "
        "WHERE origin_kind IS NULL AND title LIKE 'マインドマップ:%'"
    ))
    op.create_index("ix_notes_workspace_origin_kind", "notes", ["workspace_id", "origin_kind"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(
        "SELECT COUNT(*) FROM research_actions WHERE extraction_source IS NOT NULL OR extraction_ordinal IS NOT NULL"
    )).scalar_one():
        raise RuntimeError("Cannot downgrade 20260721_0028 while mind-map action identities exist.")
    if bind.execute(sa.text("SELECT COUNT(*) FROM notes WHERE origin_kind IS NOT NULL")).scalar_one():
        raise RuntimeError("Cannot downgrade 20260721_0028 while structured note origins exist.")
    op.drop_index("ix_notes_workspace_origin_kind", table_name="notes")
    with op.batch_alter_table("notes") as batch:
        batch.drop_column("origin_kind")
    with op.batch_alter_table("research_actions") as batch:
        batch.drop_constraint("uq_research_actions_mind_map_extraction", type_="unique")
        batch.drop_column("extraction_ordinal")
        batch.drop_column("extraction_source")
