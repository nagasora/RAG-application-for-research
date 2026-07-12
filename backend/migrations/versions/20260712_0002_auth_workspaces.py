"""Add authenticated users, workspaces, memberships, and workspace-scoped papers."""

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa

revision = "20260712_0002"
down_revision = "20260712_0001"
branch_labels = None
depends_on = None


def _legacy_id(kind: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"paperpilot:{kind}:{value}"))


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_personal", sa.Boolean(), nullable=False),
        sa.Column("personal_owner_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["personal_owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("personal_owner_id", name="uq_workspaces_personal_owner"),
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')", name="ck_workspace_members_role"
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )

    with op.batch_alter_table("papers") as batch:
        batch.add_column(sa.Column("workspace_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("created_by", sa.String(length=36), nullable=True))

    connection = op.get_bind()
    legacy_users = [row[0] for row in connection.execute(sa.text("SELECT DISTINCT user_id FROM papers"))]
    now = datetime.now(timezone.utc)
    for subject in legacy_users:
        user_id = _legacy_id("user", subject)
        workspace_id = _legacy_id("workspace", subject)
        connection.execute(
            sa.text(
                "INSERT INTO users (id, issuer, subject, email, display_name, created_at) "
                "VALUES (:id, 'paperpilot-dev', :subject, NULL, :subject, :created_at)"
            ),
            {"id": user_id, "subject": subject, "created_at": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO workspaces "
                "(id, name, is_personal, personal_owner_id, created_by, created_at) "
                "VALUES (:id, :name, :is_personal, :owner, :owner, :created_at)"
            ),
            {
                "id": workspace_id,
                "name": f"{subject} のワークスペース",
                "is_personal": True,
                "owner": user_id,
                "created_at": now,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO workspace_members (workspace_id, user_id, role, created_at) "
                "VALUES (:workspace_id, :user_id, 'owner', :created_at)"
            ),
            {"workspace_id": workspace_id, "user_id": user_id, "created_at": now},
        )
        connection.execute(
            sa.text(
                "UPDATE papers SET workspace_id=:workspace_id, created_by=:user_id "
                "WHERE user_id=:subject"
            ),
            {"workspace_id": workspace_id, "user_id": user_id, "subject": subject},
        )

    with op.batch_alter_table("papers") as batch:
        batch.alter_column("workspace_id", existing_type=sa.String(length=36), nullable=False)
        batch.alter_column("created_by", existing_type=sa.String(length=36), nullable=False)
        batch.drop_constraint("uq_papers_user_content_hash", type_="unique")
        batch.create_unique_constraint(
            "uq_papers_workspace_content_hash", ["workspace_id", "content_hash"]
        )
        batch.create_foreign_key(
            "fk_papers_workspace_id", "workspaces", ["workspace_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_foreign_key(
            "fk_papers_created_by", "users", ["created_by"], ["id"], ondelete="RESTRICT"
        )
    op.create_index("ix_papers_workspace_id", "papers", ["workspace_id"])
    op.create_index("ix_papers_created_by", "papers", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_papers_created_by", table_name="papers")
    op.drop_index("ix_papers_workspace_id", table_name="papers")
    with op.batch_alter_table("papers") as batch:
        batch.drop_constraint("fk_papers_created_by", type_="foreignkey")
        batch.drop_constraint("fk_papers_workspace_id", type_="foreignkey")
        batch.drop_constraint("uq_papers_workspace_content_hash", type_="unique")
        batch.create_unique_constraint("uq_papers_user_content_hash", ["user_id", "content_hash"])
        batch.drop_column("created_by")
        batch.drop_column("workspace_id")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("users")
