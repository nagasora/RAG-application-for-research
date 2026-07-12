from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_initial_migration_builds_current_schema(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    assert set(inspector.get_table_names()) >= {
        "alembic_version", "papers", "chunks", "users", "workspaces", "workspace_members",
        "tags", "paper_tags", "notes", "search_history", "saved_comparisons"
        , "ingestion_jobs", "paper_pages", "document_elements"
    }
    paper_columns = {column["name"] for column in inspector.get_columns("papers")}
    assert {"storage_key", "mime_type", "byte_size", "workspace_id", "created_by"} <= paper_columns


def test_workspace_migration_backfills_legacy_papers(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260712_0001")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO papers (
                id,user_id,title,authors,year,abstract,source,external_id,status,page_count,
                created_at,content_hash,error_message,storage_key,mime_type,byte_size
            ) VALUES (
                'paper-1','legacy-user','Legacy','[]',NULL,'','upload',NULL,'ready',1,
                '2026-07-12 00:00:00','hash-1',NULL,NULL,NULL,NULL
            )
        """))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        paper = connection.execute(
            text("SELECT workspace_id, created_by FROM papers WHERE id='paper-1'")
        ).one()
        member = connection.execute(text("""
            SELECT u.issuer,u.subject,m.role
            FROM users u JOIN workspace_members m ON m.user_id=u.id
            WHERE u.id=:user_id
        """), {"user_id": paper.created_by}).one()
    assert paper.workspace_id
    assert member == ("paperpilot-dev", "legacy-user", "owner")
