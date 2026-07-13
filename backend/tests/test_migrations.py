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
        , "ingestion_jobs", "paper_pages", "document_elements", "chunk_embeddings",
        "research_conversations", "research_messages", "research_memory_events", "embedding_jobs"
    }
    paper_columns = {column["name"] for column in inspector.get_columns("papers")}
    assert {"storage_key", "mime_type", "byte_size", "workspace_id", "created_by"} <= paper_columns
    conversation_columns = {
        column["name"] for column in inspector.get_columns("research_conversations")
    }
    assert {"message_count", "memory_event_count"} <= conversation_columns
    message_columns = {column["name"] for column in inspector.get_columns("research_messages")}
    assert "ordinal" in message_columns


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


def test_embedding_job_migration_backfills_existing_ready_chunks(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'embedding-backfill.db'}"
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
                'paper-ready','legacy-user','Ready','[]',NULL,'','upload',NULL,'ready',1,
                '2026-07-12 00:00:00','hash-ready',NULL,NULL,NULL,NULL
            )
        """))
    command.upgrade(config, "20260713_0005")
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO chunks (id,paper_id,page,section,text)
            VALUES ('chunk-ready','paper-ready',1,'本文','existing evidence')
        """))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        job = connection.execute(text("""
            SELECT provider,model,status,total_chunks FROM embedding_jobs
            WHERE paper_id='paper-ready'
        """)).one()
    assert job == ("openai", "text-embedding-3-small", "queued", 1)


def test_research_memory_migration_backfills_message_ordinals_and_count(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'research-memory-backfill.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260713_0007")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO users (id,issuer,subject,email,display_name,created_at)
            VALUES ('u','test','u',NULL,NULL,'2026-07-13 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO workspaces (id,name,is_personal,personal_owner_id,created_by,created_at)
            VALUES ('w','W',0,NULL,'u','2026-07-13 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO research_conversations
                (id,workspace_id,created_by,title,summary,created_at,updated_at)
            VALUES ('c','w','u','C','','2026-07-13 00:00:00','2026-07-13 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO research_messages (id,conversation_id,role,content,citations,created_at)
            VALUES
                ('m2','c','assistant','a','[]','2026-07-13 00:00:01'),
                ('m1','c','user','q','[]','2026-07-13 00:00:00')
        """))

    command.upgrade(config, "head")
    with engine.connect() as connection:
        count = connection.execute(text(
            "SELECT message_count,memory_event_count FROM research_conversations WHERE id='c'"
        )).one()
        messages = connection.execute(text(
            "SELECT id,ordinal FROM research_messages WHERE conversation_id='c' ORDER BY ordinal"
        )).all()
    assert count == (2, 0)
    assert messages == [("m1", 1), ("m2", 2)]
