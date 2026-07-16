"""Complete Idea anchors and Experiment Plan integrity.

Revision ID: 20260716_0023
Revises: 20260716_0022
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_0023"
down_revision = "20260716_0022"
branch_labels = None
depends_on = None


IDEA_KINDS = "'observation','interpretation','hypothesis','falsifier','todo'"
IDEA_STATUSES = "'unverified','promoted'"


def upgrade():
    op.create_table(
        "idea_integrity_migration_audit",
        sa.Column("idea_id", sa.String(36), primary_key=True),
        sa.Column("original_kind", sa.String(24), nullable=False),
        sa.Column("original_status", sa.String(24), nullable=False),
        sa.Column("original_created_by", sa.String(36)),
        sa.Column("original_research_run_id", sa.String(36)),
        sa.Column("original_paper_id", sa.String(36)),
        sa.Column("original_source_span_id", sa.String(36)),
        sa.Column("original_hypothesis_card_id", sa.String(36)),
        sa.Column("normalization_reason", sa.Text(), nullable=False),
        sa.Column("normalized_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(f"""
        INSERT INTO idea_integrity_migration_audit (
            idea_id, original_kind, original_status, original_created_by,
            original_research_run_id, original_paper_id, original_source_span_id,
            original_hypothesis_card_id, normalization_reason, normalized_at
        )
        SELECT id, kind, status, created_by, research_run_id, paper_id,
               source_span_id, hypothesis_card_id,
               'normalized invalid enum or orphan anchor during 0023', CURRENT_TIMESTAMP
        FROM ideas
        WHERE kind NOT IN ({IDEA_KINDS})
           OR status NOT IN ({IDEA_STATUSES})
           OR (created_by IS NOT NULL AND created_by NOT IN (SELECT id FROM users))
           OR (research_run_id IS NOT NULL AND research_run_id NOT IN (SELECT id FROM research_runs))
           OR (paper_id IS NOT NULL AND paper_id NOT IN (SELECT id FROM papers))
           OR (source_span_id IS NOT NULL AND source_span_id NOT IN (SELECT id FROM source_spans))
           OR (hypothesis_card_id IS NOT NULL AND hypothesis_card_id NOT IN (SELECT id FROM hypothesis_cards))
    """)
    # Earlier revisions accepted unconstrained string anchors.  Preserve rows
    # while removing orphan references before enforcing their real ownership.
    op.execute(f"UPDATE ideas SET kind='hypothesis' WHERE kind NOT IN ({IDEA_KINDS})")
    op.execute(f"UPDATE ideas SET status='unverified' WHERE status NOT IN ({IDEA_STATUSES})")
    op.execute("UPDATE ideas SET created_by=NULL WHERE created_by IS NOT NULL AND created_by NOT IN (SELECT id FROM users)")
    op.execute("UPDATE ideas SET research_run_id=NULL WHERE research_run_id IS NOT NULL AND research_run_id NOT IN (SELECT id FROM research_runs)")
    op.execute("UPDATE ideas SET paper_id=NULL WHERE paper_id IS NOT NULL AND paper_id NOT IN (SELECT id FROM papers)")
    op.execute("UPDATE ideas SET source_span_id=NULL WHERE source_span_id IS NOT NULL AND source_span_id NOT IN (SELECT id FROM source_spans)")
    op.execute("UPDATE ideas SET hypothesis_card_id=NULL WHERE hypothesis_card_id IS NOT NULL AND hypothesis_card_id NOT IN (SELECT id FROM hypothesis_cards)")
    op.execute("UPDATE experiment_plans SET hypothesis_card_id=NULL WHERE hypothesis_card_id IS NOT NULL AND hypothesis_card_id NOT IN (SELECT id FROM hypothesis_cards)")

    with op.batch_alter_table("ideas", recreate="always") as batch:
        batch.create_check_constraint("ck_ideas_kind", f"kind IN ({IDEA_KINDS})")
        batch.create_check_constraint("ck_ideas_status", f"status IN ({IDEA_STATUSES})")
        batch.create_foreign_key("fk_ideas_created_by_users", "users", ["created_by"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_ideas_research_run", "research_runs", ["research_run_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_ideas_paper", "papers", ["paper_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_ideas_source_span", "source_spans", ["source_span_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_ideas_hypothesis_card", "hypothesis_cards", ["hypothesis_card_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_ideas_workspace_status_created", "ideas", ["workspace_id", "status", "created_at"])
    op.create_index("ix_ideas_research_run", "ideas", ["research_run_id"])
    op.create_index("ix_ideas_paper", "ideas", ["paper_id"])
    op.create_index("ix_ideas_source_span", "ideas", ["source_span_id"])
    op.create_index("ix_ideas_hypothesis_card", "ideas", ["hypothesis_card_id"])

    with op.batch_alter_table("hypothesis_cards") as batch:
        batch.add_column(sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

    with op.batch_alter_table("experiment_plans", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_experiment_plans_hypothesis_card", "hypothesis_cards",
            ["hypothesis_card_id"], ["id"], ondelete="SET NULL",
        )
    op.create_index("ix_experiment_plans_workspace_updated", "experiment_plans", ["workspace_id", "updated_at"])
    op.create_index("ix_experiment_plans_hypothesis_card", "experiment_plans", ["hypothesis_card_id"])


def downgrade():
    audit_count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM idea_integrity_migration_audit")
    ).scalar_one()
    if audit_count:
        raise RuntimeError(
            "Cannot downgrade 20260716_0023: idea integrity audit rows would be discarded"
        )
    op.drop_index("ix_experiment_plans_hypothesis_card", table_name="experiment_plans")
    op.drop_index("ix_experiment_plans_workspace_updated", table_name="experiment_plans")
    with op.batch_alter_table("experiment_plans", recreate="always") as batch:
        batch.drop_constraint("fk_experiment_plans_hypothesis_card", type_="foreignkey")
    with op.batch_alter_table("hypothesis_cards") as batch:
        batch.drop_column("metadata_json")

    op.drop_index("ix_ideas_hypothesis_card", table_name="ideas")
    op.drop_index("ix_ideas_source_span", table_name="ideas")
    op.drop_index("ix_ideas_paper", table_name="ideas")
    op.drop_index("ix_ideas_research_run", table_name="ideas")
    op.drop_index("ix_ideas_workspace_status_created", table_name="ideas")
    with op.batch_alter_table("ideas", recreate="always") as batch:
        batch.drop_constraint("fk_ideas_hypothesis_card", type_="foreignkey")
        batch.drop_constraint("fk_ideas_source_span", type_="foreignkey")
        batch.drop_constraint("fk_ideas_paper", type_="foreignkey")
        batch.drop_constraint("fk_ideas_research_run", type_="foreignkey")
        batch.drop_constraint("fk_ideas_created_by_users", type_="foreignkey")
        batch.drop_constraint("ck_ideas_status", type_="check")
        batch.drop_constraint("ck_ideas_kind", type_="check")
    op.drop_table("idea_integrity_migration_audit")
