from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text


def _migration_config(tmp_path, monkeypatch, filename: str) -> tuple[Config, str]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / filename}"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config, database_url


def test_idea_claim_idempotency_migration_upgrades_and_downgrades(tmp_path, monkeypatch):
    config, database_url = _migration_config(tmp_path, monkeypatch, "idea-claim-migration.db")

    command.upgrade(config, "20260719_0025")
    before = {item["name"] for item in inspect(create_engine(database_url)).get_columns("ideas")}
    assert {"claim_artifact_id", "claim_snapshot", "idempotency_key"}.isdisjoint(before)

    command.upgrade(config, "head")
    upgraded = {item["name"] for item in inspect(create_engine(database_url)).get_columns("ideas")}
    assert {"claim_artifact_id", "claim_snapshot", "idempotency_key"} <= upgraded
    unique_constraints = {item["name"] for item in inspect(create_engine(database_url)).get_unique_constraints("ideas")}
    assert "uq_ideas_idempotency_key" in unique_constraints

    command.downgrade(config, "20260719_0025")
    downgraded = {item["name"] for item in inspect(create_engine(database_url)).get_columns("ideas")}
    assert {"claim_artifact_id", "claim_snapshot", "idempotency_key"}.isdisjoint(downgraded)


def test_idea_claim_idempotency_upgrade_preserves_legacy_idea_rows(tmp_path, monkeypatch):
    config, database_url = _migration_config(tmp_path, monkeypatch, "legacy-idea-claim-migration.db")
    command.upgrade(config, "20260719_0025")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO ideas (
                id, workspace_id, created_by, kind, content, research_run_id,
                claim_id, paper_id, source_span_id, checklist, status,
                hypothesis_card_id, created_at
            ) VALUES (
                'legacy-idea', 'legacy-workspace', NULL, 'hypothesis',
                'preserve this legacy Idea', NULL, NULL, NULL, NULL,
                '{"evidence": true}', 'unverified', NULL, CURRENT_TIMESTAMP
            )
        """))

    command.upgrade(config, "20260719_0026")
    with create_engine(database_url).connect() as connection:
        row = connection.execute(text("""
            SELECT id, content, checklist, claim_artifact_id, claim_snapshot, idempotency_key
            FROM ideas WHERE id='legacy-idea'
        """)).mappings().one()
    assert row["id"] == "legacy-idea"
    assert row["content"] == "preserve this legacy Idea"
    assert row["checklist"] == '{"evidence": true}'
    assert row["claim_artifact_id"] is None
    assert row["claim_snapshot"] is None
    assert row["idempotency_key"] is None


def test_idea_claim_idempotency_downgrade_refuses_and_preserves_claim_metadata(tmp_path, monkeypatch):
    config, database_url = _migration_config(tmp_path, monkeypatch, "protected-idea-claim-migration.db")
    command.upgrade(config, "20260719_0026")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO ideas (
                id, workspace_id, created_by, kind, content, research_run_id,
                claim_id, paper_id, source_span_id, checklist, status,
                hypothesis_card_id, created_at, claim_artifact_id, claim_snapshot,
                idempotency_key
            ) VALUES (
                'protected-idea', 'protected-workspace', NULL, 'hypothesis',
                'claim metadata must survive', NULL, NULL, NULL, NULL,
                '{}', 'unverified', NULL, CURRENT_TIMESTAMP, NULL,
                '{"claim_id":"claim-1","text":"snapshot"}',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            )
        """))

    with pytest.raises(RuntimeError, match="claim-anchored Idea metadata exists"):
        command.downgrade(config, "20260719_0025")

    columns = {item["name"] for item in inspect(create_engine(database_url)).get_columns("ideas")}
    assert {"claim_artifact_id", "claim_snapshot", "idempotency_key"} <= columns
    with create_engine(database_url).connect() as connection:
        row = connection.execute(text("""
            SELECT claim_snapshot, idempotency_key FROM ideas WHERE id='protected-idea'
        """)).mappings().one()
    assert row["claim_snapshot"] == '{"claim_id":"claim-1","text":"snapshot"}'
    assert row["idempotency_key"] == "a" * 64
