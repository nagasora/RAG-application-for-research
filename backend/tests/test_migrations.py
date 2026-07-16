from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
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
        "research_conversations", "research_messages", "research_memory_events", "embedding_jobs",
        "knowledge_edge_status_events", "research_questions", "source_sets", "source_set_papers",
        "research_runs", "run_artifacts",
        "review_threads", "review_comments", "review_decisions",
    }
    paper_columns = {column["name"] for column in inspector.get_columns("papers")}
    assert {"storage_key", "mime_type", "byte_size", "workspace_id", "created_by"} <= paper_columns
    conversation_columns = {
        column["name"] for column in inspector.get_columns("research_conversations")
    }
    assert {"message_count", "memory_event_count"} <= conversation_columns
    message_columns = {column["name"] for column in inspector.get_columns("research_messages")}
    assert "ordinal" in message_columns
    evidence_columns = {column["name"] for column in inspector.get_columns("evidence_refs")}
    assert {
        "source_version_id", "target_claim", "role", "extraction_quality",
        "quote_start", "quote_end", "verbatim_quote",
    } <= evidence_columns
    hypothesis_columns = {column["name"] for column in inspector.get_columns("hypothesis_cards")}
    assert "metadata_json" in hypothesis_columns
    idea_foreign_keys = {
        tuple(item["constrained_columns"]): (item["referred_table"], item["options"].get("ondelete"))
        for item in inspector.get_foreign_keys("ideas")
    }
    assert idea_foreign_keys[("research_run_id",)] == ("research_runs", "SET NULL")
    assert idea_foreign_keys[("paper_id",)] == ("papers", "SET NULL")
    assert idea_foreign_keys[("source_span_id",)] == ("source_spans", "SET NULL")
    assert idea_foreign_keys[("hypothesis_card_id",)] == ("hypothesis_cards", "SET NULL")
    idea_checks = {item["name"] for item in inspector.get_check_constraints("ideas")}
    assert {"ck_ideas_kind", "ck_ideas_status"} <= idea_checks
    idea_indexes = {item["name"] for item in inspector.get_indexes("ideas")}
    assert {
        "ix_ideas_workspace_status_created", "ix_ideas_research_run", "ix_ideas_paper",
        "ix_ideas_source_span", "ix_ideas_hypothesis_card",
    } <= idea_indexes
    experiment_foreign_keys = {
        tuple(item["constrained_columns"]): item["referred_table"]
        for item in inspector.get_foreign_keys("experiment_plans")
    }
    assert experiment_foreign_keys[("hypothesis_card_id",)] == "hypothesis_cards"
    review_foreign_keys = {
        tuple(item["constrained_columns"]): (item["referred_table"], item["options"].get("ondelete"))
        for item in inspector.get_foreign_keys("review_threads")
    }
    assert review_foreign_keys[("research_run_id",)] == ("research_runs", "RESTRICT")
    assert review_foreign_keys[("claim_artifact_id",)] == ("run_artifacts", "RESTRICT")
    assert review_foreign_keys[("evidence_ref_id",)] == ("evidence_refs", "RESTRICT")


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


def test_source_identity_downgrade_refuses_ambiguous_versions_without_changing_data(tmp_path, monkeypatch):
    """0010 must not silently collapse distinct immutable provenance records."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'source-identity.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    content_hash = "a" * 64
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO users (id,issuer,subject,email,display_name,created_at)
            VALUES ('source-user','test','source-user',NULL,NULL,'2026-07-13 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO workspaces (id,name,is_personal,personal_owner_id,created_by,created_at)
            VALUES ('source-workspace','Source workspace',0,NULL,'source-user','2026-07-13 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO source_versions
                (id,workspace_id,paper_id,kind,locator,content_hash,metadata_json,created_at)
            VALUES
                ('source-python','source-workspace',NULL,'python','repo://model.py@a',:hash,'{}','2026-07-13 00:00:00'),
                ('source-markdown','source-workspace',NULL,'markdown','note://model',:hash,'{}','2026-07-13 00:00:00')
        """), {"hash": content_hash})

    with pytest.raises(RuntimeError, match="Cannot downgrade 20260713_0010_source_import_identity"):
        command.downgrade(config, "20260713_0009")

    with engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM source_versions")).scalar_one()
    constraints = {item["name"] for item in inspect(engine).get_unique_constraints("source_versions")}
    assert count == 2
    assert "uq_source_versions_workspace_kind_locator_content_hash" in constraints


def test_edge_lifecycle_downgrade_refuses_to_discard_audit_history(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'edge-lifecycle.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO users (id,issuer,subject,email,display_name,created_at)
            VALUES ('edge-user','test','edge-user',NULL,NULL,'2026-07-13 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO workspaces (id,name,is_personal,personal_owner_id,created_by,created_at)
            VALUES ('edge-workspace','Edge workspace',0,NULL,'edge-user','2026-07-13 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO knowledge_nodes
                (id,workspace_id,created_by,node_type,status,layer,content,phase,confidence,
                 metadata_json,created_at,updated_at)
            VALUES
                ('edge-source','edge-workspace','edge-user','source','active',0,'source','grounded',1,'{}','2026-07-13 00:00:00','2026-07-13 00:00:00'),
                ('edge-target','edge-workspace','edge-user','hypothesis','active',1,'target','hypothesis',1,'{}','2026-07-13 00:00:00','2026-07-13 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO knowledge_edges
                (id,workspace_id,created_by,source_node_id,target_node_id,relation,status,origin,
                 metadata_json,created_at,updated_at)
            VALUES
                ('edge-1','edge-workspace','edge-user','edge-source','edge-target','informs',
                 'rejected','manual','{}','2026-07-13 00:00:00','2026-07-13 00:01:00')
        """))
        connection.execute(text("""
            INSERT INTO knowledge_edge_status_events
                (id,workspace_id,knowledge_edge_id,actor_id,from_status,to_status,reason,created_at)
            VALUES
                ('event-1','edge-workspace','edge-1','edge-user','active','rejected',
                 'evidence did not support this relation','2026-07-13 00:01:00')
        """))

    with pytest.raises(RuntimeError, match="Cannot downgrade 20260713_0011_edge_lifecycle"):
        command.downgrade(config, "20260713_0010")

    with engine.connect() as connection:
        event = connection.execute(text(
            "SELECT from_status,to_status,reason FROM knowledge_edge_status_events WHERE id='event-1'"
        )).one()
        edge = connection.execute(text(
            "SELECT status,origin,created_by FROM knowledge_edges WHERE id='edge-1'"
        )).one()
    assert event == ("active", "rejected", "evidence did not support this relation")
    assert edge == ("rejected", "manual", "edge-user")


def test_idea_integrity_migration_archives_dirty_legacy_values(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'idea-dirty.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260716_0022")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO users (id,issuer,subject,email,display_name,created_at)
            VALUES ('idea-user','test','idea-user',NULL,NULL,'2026-07-16 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO workspaces (id,name,is_personal,personal_owner_id,created_by,created_at)
            VALUES ('idea-workspace','Ideas',0,NULL,'idea-user','2026-07-16 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO ideas (
                id,workspace_id,created_by,kind,content,research_run_id,claim_id,
                paper_id,source_span_id,checklist,status,hypothesis_card_id,created_at
            ) VALUES (
                'dirty-idea','idea-workspace','missing-user','legacy_kind','legacy idea',
                'missing-run','claim','missing-paper','missing-span','{}','legacy_status',
                'missing-card','2026-07-16 00:00:00'
            )
        """))

    command.upgrade(config, "20260716_0023")
    with engine.connect() as connection:
        normalized = connection.execute(text("""
            SELECT kind,status,created_by,research_run_id,paper_id,source_span_id,hypothesis_card_id
            FROM ideas WHERE id='dirty-idea'
        """)).one()
        audit = connection.execute(text("""
            SELECT original_kind,original_status,original_created_by,original_research_run_id,
                   original_paper_id,original_source_span_id,original_hypothesis_card_id
            FROM idea_integrity_migration_audit WHERE idea_id='dirty-idea'
        """)).one()
    assert normalized == ("hypothesis", "unverified", None, None, None, None, None)
    assert audit == (
        "legacy_kind", "legacy_status", "missing-user", "missing-run",
        "missing-paper", "missing-span", "missing-card",
    )
    with pytest.raises(RuntimeError, match="idea integrity audit rows would be discarded"):
        command.downgrade(config, "20260716_0022")


def test_collaborative_review_downgrade_refuses_to_drop_audit_history(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'review-audit.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO users (id,issuer,subject,email,display_name,created_at)
            VALUES ('review-user','test','review-user',NULL,NULL,'2026-07-16 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO workspaces (id,name,is_personal,personal_owner_id,created_by,created_at)
            VALUES ('review-workspace','Reviews',0,NULL,'review-user','2026-07-16 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO research_runs (
                id,workspace_id,created_by,research_question_id,source_set_id,research_question,
                source_paper_ids,excluded_paper_ids,purpose,success_criteria,plan,model,
                prompt_version,status,cancel_requested,started_at,completed_at,created_at
            ) VALUES (
                'review-run','review-workspace','review-user',NULL,NULL,'','[]','[]','','','{}','','',
                'queued',0,NULL,NULL,'2026-07-16 00:00:00'
            )
        """))
        connection.execute(text("""
            INSERT INTO run_artifacts (id,research_run_id,kind,payload,ordinal,created_at)
            VALUES ('review-artifact','review-run','validation',
                    '{"claims":[{"claim_id":"claim-1","text":"claim"}]}',1,
                    '2026-07-16 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO review_threads (
                id,workspace_id,created_by,title,research_run_id,claim_id,claim_artifact_id,
                claim_snapshot,evidence_ref_id,assigned_to,status,created_at,updated_at
            ) VALUES (
                'review-thread','review-workspace','review-user','Audit','review-run','claim-1',
                'review-artifact','{"claim_id":"claim-1","text":"claim"}',NULL,NULL,'open',
                '2026-07-16 00:00:00','2026-07-16 00:00:00'
            )
        """))
    with pytest.raises(RuntimeError, match="review audit data exists"):
        command.downgrade(config, "20260716_0023")
