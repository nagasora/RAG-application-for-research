from pathlib import Path
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, text


def _insert_required_row(engine, table_name: str, **overrides):
    """Insert one migration-guard fixture without depending on current ORM models."""
    table = Table(table_name, MetaData(), autoload_with=engine)
    values = dict(overrides)
    for column in table.columns:
        if column.name in values or column.nullable or column.server_default is not None:
            continue
        try:
            python_type = column.type.python_type
        except NotImplementedError:
            python_type = str
        if python_type is str:
            values[column.name] = f"{table_name}-{column.name}"
        elif python_type is int:
            values[column.name] = 1
        elif python_type is bool:
            values[column.name] = False
        elif python_type is datetime:
            values[column.name] = datetime.now(timezone.utc)
        elif python_type is dict:
            values[column.name] = {}
        elif python_type is list:
            values[column.name] = []
        elif python_type is bytes:
            values[column.name] = b"x"
        else:
            values[column.name] = python_type()
    with engine.begin() as connection:
        connection.execute(table.insert().values(**values))


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
        "discovery_search_sessions", "discovery_search_candidates",
        "workspace_generation_settings", "generation_audits", "experiment_profiles",
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


def test_discovery_search_migration_backfills_normalized_external_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'discovery-migration.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260721_0028")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO users (id,issuer,subject,email,display_name,created_at)
            VALUES ('u','test','legacy',NULL,NULL,'2026-07-21 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO workspaces (id,name,is_personal,personal_owner_id,created_by,created_at)
            VALUES ('w','Legacy',0,NULL,'u','2026-07-21 00:00:00')
        """))
        connection.execute(text("""
            INSERT INTO papers
            (id,workspace_id,created_by,user_id,title,authors,year,abstract,source,external_id,status,page_count,created_at,content_hash,error_message,storage_key,mime_type,byte_size)
            VALUES ('p','w','u','legacy','Legacy','[]',2024,'Abstract','DOI','https://doi.org/10.1000/ABC/','ready',1,'2026-07-21 00:00:00','legacy-hash',NULL,NULL,NULL,NULL)
        """))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        paper = connection.execute(text("SELECT content_scope FROM papers WHERE id='p'")).scalar_one()
        identity = connection.execute(text("SELECT provider,identifier FROM paper_external_identifiers WHERE paper_id='p'")).one()
        discovery_columns = {item["name"] for item in inspect(engine).get_columns("discovery_items")}
    assert paper == "abstract_only"
    assert identity == ("doi", "10.1000/abc")
    assert {"paper_id", "search_context"} <= discovery_columns
    command.downgrade(config, "20260721_0028")
    downgraded = inspect(create_engine(database_url))
    assert "paper_external_identifiers" not in downgraded.get_table_names()
    assert "content_scope" not in {item["name"] for item in downgraded.get_columns("papers")}


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


def test_federated_discovery_generation_migration_upgrades_and_downgrades(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'federated-discovery.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "20260727_0029")
    command.upgrade(config, "20260727_0030")
    engine = create_engine(database_url)
    upgraded = inspect(engine)
    assert {
        "discovery_search_sessions",
        "discovery_search_candidates",
        "workspace_generation_settings",
        "generation_audits",
        "experiment_profiles",
    } <= set(upgraded.get_table_names())
    assert "generation_provider" in {
        column["name"] for column in upgraded.get_columns("research_runs")
    }

    command.downgrade(config, "20260727_0029")
    downgraded = inspect(engine)
    assert not {
        "discovery_search_sessions",
        "discovery_search_candidates",
        "workspace_generation_settings",
        "generation_audits",
        "experiment_profiles",
    } & set(downgraded.get_table_names())
    assert "generation_provider" not in {
        column["name"] for column in downgraded.get_columns("research_runs")
    }


@pytest.mark.parametrize("guard_table", [
    "experiment_profiles",
    "generation_audits",
    "workspace_generation_settings",
    "discovery_search_candidates",
    "discovery_search_sessions",
])
def test_federated_discovery_downgrade_refuses_each_nonempty_guard_table(
    tmp_path, monkeypatch, guard_table,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / f'guard-{guard_table}.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260727_0030")
    engine = create_engine(database_url)
    _insert_required_row(engine, guard_table)

    with pytest.raises(RuntimeError, match=guard_table):
        command.downgrade(config, "20260727_0029")

    assert guard_table in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text(f"SELECT COUNT(*) FROM {guard_table}")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260727_0030"


def test_federated_discovery_downgrade_refuses_nonempty_research_run_generation(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'guard-research-run.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260727_0030")
    engine = create_engine(database_url)
    _insert_required_row(engine, "research_runs", generation_provider="openai")

    with pytest.raises(RuntimeError, match="research_run_generation"):
        command.downgrade(config, "20260727_0029")

    assert "generation_provider" in {
        column["name"] for column in inspect(engine).get_columns("research_runs")
    }
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT generation_provider FROM research_runs"
        )).scalar_one() == "openai"
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260727_0030"


def test_saved_comparison_analysis_errors_downgrade_guard_is_lossless(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'guard-analysis-errors.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260728_0031")
    engine = create_engine(database_url)
    _insert_required_row(
        engine, "saved_comparisons",
        analysis_errors=[{"paper_id": "excluded", "code": "analysis_failed"}],
    )

    with pytest.raises(RuntimeError, match="saved_comparisons.analysis_errors"):
        command.downgrade(config, "20260727_0030")

    assert "analysis_errors" in {
        column["name"] for column in inspect(engine).get_columns("saved_comparisons")
    }
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT analysis_errors FROM saved_comparisons"
        )).scalar_one()
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260728_0031"


def test_saved_comparison_analysis_errors_empty_database_downgrades(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'empty-analysis-errors.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260728_0031")
    command.downgrade(config, "20260727_0030")
    assert "analysis_errors" not in {
        column["name"] for column in inspect(create_engine(database_url)).get_columns("saved_comparisons")
    }


def test_mind_map_downgrade_preserves_detached_note_provenance(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'mind-map-note-guard.db'}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260729_0032")
    engine = create_engine(database_url)
    _insert_required_row(engine, "users", id="u", issuer="test", subject="mind-map-user")
    _insert_required_row(engine, "workspaces", id="w", created_by="u", is_personal=False)
    _insert_required_row(
        engine,
        "notes",
        id="n",
        workspace_id="w",
        author_id="u",
        origin_kind="mind_map",
        origin_snapshot={"mind_map_id": "deleted", "node_id": "deleted-node", "title": "root"},
    )

    with pytest.raises(RuntimeError, match="Note provenance"):
        command.downgrade(config, "20260728_0031")

    assert "origin_snapshot" in {
        column["name"] for column in inspect(engine).get_columns("notes")
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT origin_snapshot FROM notes WHERE id='n'")).scalar_one()
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260729_0032"
