"""Persist claim-level immutable EvidenceLinks.

Revision ID: 20260716_0013
Revises: 20260714_0012
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_0013"
down_revision = "20260714_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable fields first so existing audit records can be populated from
    # their immutable SourceSpan before the non-null contract is enabled.
    with op.batch_alter_table("evidence_refs") as batch_op:
        batch_op.add_column(sa.Column("source_version_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("target_claim", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("role", sa.String(length=16), nullable=False, server_default="supports"))
        batch_op.add_column(sa.Column("extraction_quality", sa.String(length=16), nullable=False, server_default="unknown"))
        batch_op.add_column(sa.Column("quote_start", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("quote_end", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("verbatim_quote", sa.Text(), nullable=False, server_default=""))

    bind = op.get_bind()
    # SQLite and PostgreSQL both support correlated scalar subqueries here.
    bind.execute(sa.text("""
        UPDATE evidence_refs
        SET source_version_id = (
                SELECT source_version_id FROM source_spans
                WHERE source_spans.id = evidence_refs.source_span_id
            ),
            quote_start = 0,
            quote_end = COALESCE((
                SELECT length(text) FROM source_spans
                WHERE source_spans.id = evidence_refs.source_span_id
            ), 0),
            verbatim_quote = COALESCE((
                SELECT text FROM source_spans
                WHERE source_spans.id = evidence_refs.source_span_id
            ), '')
    """))
    with op.batch_alter_table("evidence_refs") as batch_op:
        batch_op.alter_column("source_version_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.create_foreign_key(
            "fk_evidence_refs_source_version_id_source_versions",
            "source_versions", ["source_version_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_index("ix_evidence_refs_source_version_id", ["source_version_id"])
        batch_op.create_check_constraint(
            "ck_evidence_refs_role", "role IN ('supports', 'contradicts', 'context', 'mentions')",
        )
        batch_op.create_check_constraint(
            "ck_evidence_refs_extraction_quality",
            "extraction_quality IN ('high', 'medium', 'low', 'unknown')",
        )
        batch_op.create_check_constraint(
            "ck_evidence_refs_quote_offsets", "quote_start >= 0 AND quote_end >= quote_start",
        )


def downgrade() -> None:
    # The additional fields are required to reproduce exact historical quotes;
    # dropping nonempty evidence would silently lose audit data.
    bind = op.get_bind()
    count = bind.execute(sa.text("""
        SELECT COUNT(*) FROM evidence_refs
        WHERE target_claim != '' OR role != 'supports' OR extraction_quality != 'unknown'
           OR quote_start != 0 OR verbatim_quote != ''
    """)).scalar_one()
    if count:
        raise RuntimeError(
            "Cannot downgrade 20260716_0013_evidence_links while EvidenceLink audit data exists."
        )
    with op.batch_alter_table("evidence_refs") as batch_op:
        batch_op.drop_constraint("ck_evidence_refs_quote_offsets", type_="check")
        batch_op.drop_constraint("ck_evidence_refs_extraction_quality", type_="check")
        batch_op.drop_constraint("ck_evidence_refs_role", type_="check")
        batch_op.drop_index("ix_evidence_refs_source_version_id")
        batch_op.drop_constraint("fk_evidence_refs_source_version_id_source_versions", type_="foreignkey")
        batch_op.drop_column("verbatim_quote")
        batch_op.drop_column("quote_end")
        batch_op.drop_column("quote_start")
        batch_op.drop_column("extraction_quality")
        batch_op.drop_column("role")
        batch_op.drop_column("target_claim")
        batch_op.drop_column("source_version_id")
