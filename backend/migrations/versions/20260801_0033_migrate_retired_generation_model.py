"""Migrate retired OpenAI workspace generation settings.

Revision ID: 20260801_0033
Revises: 20260729_0032
"""
from alembic import op
import sqlalchemy as sa


revision = "20260801_0033"
down_revision = "20260729_0032"
branch_labels = None
depends_on = None

_SCOPES = ("ask", "discovery", "analysis", "mind_map")
_OLD_MODEL = "gpt-5.4-nano"
_NEW_CHOICE = {"provider": "openai", "model": "gpt-5.6-luna"}


def _migrated_scopes(scopes):
    current = dict(scopes) if isinstance(scopes, dict) else {}
    migrated = dict(current)
    for scope in _SCOPES:
        choice = current.get(scope)
        if not isinstance(choice, dict):
            migrated[scope] = dict(_NEW_CHOICE)
            continue
        provider = str(choice.get("provider") or "").strip().casefold()
        model = str(choice.get("model") or "").strip()
        if provider == "openai" and model == _OLD_MODEL:
            migrated[scope] = dict(_NEW_CHOICE)
    return migrated


def upgrade() -> None:
    bind = op.get_bind()
    settings = sa.table(
        "workspace_generation_settings",
        sa.column("workspace_id", sa.String()),
        sa.column("scopes", sa.JSON()),
    )
    rows = bind.execute(sa.select(settings.c.workspace_id, settings.c.scopes)).mappings()
    for row in rows:
        scopes = _migrated_scopes(row["scopes"])
        if scopes != row["scopes"]:
            bind.execute(
                settings.update().where(settings.c.workspace_id == row["workspace_id"]).values(scopes=scopes)
            )


def downgrade() -> None:
    # The old model is retired. Reintroducing it would make stored settings
    # invalid, so downgrade intentionally leaves compatibility-normalized data.
    pass
