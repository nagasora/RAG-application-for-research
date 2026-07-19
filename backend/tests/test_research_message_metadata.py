import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_research_message_response_metadata_migration_upgrades_and_downgrades(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'metadata-migration.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "20260716_0024")
    engine = create_engine(database_url)
    before = {column["name"] for column in inspect(engine).get_columns("research_messages")}
    assert "response_metadata" not in before
    # This is a row created before CI-023: its metadata must be backfilled as
    # an empty object instead of falsely claiming a synthesis mode or draft.
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO research_messages (
                id, conversation_id, ordinal, role, content, citations, created_at
            ) VALUES (
                'legacy-message', 'legacy-conversation', 1, 'assistant',
                'legacy answer', '[]', CURRENT_TIMESTAMP
            )
        """))

    command.upgrade(config, "head")
    upgraded = {column["name"] for column in inspect(create_engine(database_url)).get_columns("research_messages")}
    assert "response_metadata" in upgraded
    with create_engine(database_url).connect() as connection:
        backfilled = connection.execute(text(
            "SELECT response_metadata FROM research_messages WHERE id='legacy-message'"
        )).scalar_one()
    assert json.loads(backfilled) == {}

    command.downgrade(config, "20260716_0024")
    downgraded = {column["name"] for column in inspect(create_engine(database_url)).get_columns("research_messages")}
    assert "response_metadata" not in downgraded
