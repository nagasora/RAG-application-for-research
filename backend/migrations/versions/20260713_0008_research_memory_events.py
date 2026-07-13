"""Add append-only, paged research memory storage."""

from alembic import op
import sqlalchemy as sa


revision = "20260713_0008"
down_revision = "20260713_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column(
        "research_conversations",
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "research_conversations",
        sa.Column("memory_event_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("research_messages", sa.Column("ordinal", sa.Integer(), nullable=True))

    # The tie-breaker by id makes legacy rows deterministic even when they share
    # a timestamp. New writes use the locked conversation counters instead.
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY conversation_id ORDER BY created_at, id
                ) AS ordinal
                FROM research_messages
            )
            UPDATE research_messages AS target
            SET ordinal = ranked.ordinal
            FROM ranked
            WHERE target.id = ranked.id
        """))
    else:
        # SQLite test/desktop compatibility. Production PostgreSQL uses the
        # set-based window-function path above instead of this fallback.
        op.execute(sa.text("""
            UPDATE research_messages AS target
            SET ordinal = (
                SELECT COUNT(*)
                FROM research_messages AS candidate
                WHERE candidate.conversation_id = target.conversation_id
                  AND (
                    candidate.created_at < target.created_at
                    OR (candidate.created_at = target.created_at AND candidate.id <= target.id)
                  )
            )
        """))
    op.execute(sa.text("""
        UPDATE research_conversations
        SET message_count = (
            SELECT COUNT(*) FROM research_messages
            WHERE research_messages.conversation_id = research_conversations.id
        )
    """))
    with op.batch_alter_table("research_messages") as batch_op:
        batch_op.alter_column("ordinal", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint(
            "uq_research_messages_conversation_ordinal", ["conversation_id", "ordinal"]
        )
        batch_op.create_index(
            "ix_research_messages_conversation_ordinal", ["conversation_id", "ordinal"]
        )

    op.create_table(
        "research_memory_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("source_message_id", sa.String(length=36), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('hypothesis', 'assumption', 'unresolved_question', 'planned_test')",
            name="ck_research_memory_events_kind",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["research_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["research_messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "ordinal",
            name="uq_research_memory_events_conversation_ordinal",
        ),
        sa.UniqueConstraint(
            "conversation_id", "kind", "content_hash",
            name="uq_research_memory_events_conversation_kind_hash",
        ),
    )
    op.create_index(
        "ix_research_memory_events_workspace_id", "research_memory_events", ["workspace_id"]
    )
    op.create_index(
        "ix_research_memory_events_conversation_id", "research_memory_events", ["conversation_id"]
    )
    op.create_index(
        "ix_research_memory_events_source_message_id", "research_memory_events", ["source_message_id"]
    )
    op.create_index(
        "ix_research_memory_events_workspace_conversation_ordinal",
        "research_memory_events", ["workspace_id", "conversation_id", "ordinal"],
    )
    op.create_index(
        "ix_research_memory_events_conversation_kind_ordinal",
        "research_memory_events", ["conversation_id", "kind", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_memory_events_conversation_kind_ordinal",
        table_name="research_memory_events",
    )
    op.drop_index(
        "ix_research_memory_events_workspace_conversation_ordinal",
        table_name="research_memory_events",
    )
    op.drop_index("ix_research_memory_events_source_message_id", table_name="research_memory_events")
    op.drop_index("ix_research_memory_events_conversation_id", table_name="research_memory_events")
    op.drop_index("ix_research_memory_events_workspace_id", table_name="research_memory_events")
    op.drop_table("research_memory_events")
    with op.batch_alter_table("research_messages") as batch_op:
        batch_op.drop_index("ix_research_messages_conversation_ordinal")
        batch_op.drop_constraint("uq_research_messages_conversation_ordinal", type_="unique")
        batch_op.drop_column("ordinal")
    op.drop_column("research_conversations", "memory_event_count")
    op.drop_column("research_conversations", "message_count")
