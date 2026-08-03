from datetime import datetime, timezone
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, WorkspaceGenerationSettingsRecord
from app.generation_service import resolve_workspace_generation
from app.models import Principal
from app.store import PaperStore


def _setup(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'settings.db'}",connect_args={"check_same_thread":False})
    Base.metadata.create_all(engine)
    main.app.dependency_overrides[main.get_store]=lambda:PaperStore(session_factory=sessionmaker(bind=engine,expire_on_commit=False))


def test_generation_settings_are_owner_governed_and_never_enable_missing_gemini_key(tmp_path, monkeypatch):
    _setup(tmp_path); monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    try:
        with TestClient(main.app) as api:
            owner={"X-Dev-User":"owner"}
            workspace=api.post("/api/workspaces",headers=owner,json={"name":"shared"}).json()
            api.get("/api/me",headers={"X-Dev-User":"editor"})
            api.post(f"/api/workspaces/{workspace['id']}/members",headers=owner,json={"subject":"editor","role":"editor"})
            current={"X-Dev-User":"editor","X-Workspace-ID":workspace["id"]}
            catalog=api.get("/api/workspace/generation-settings",headers=current)
            blocked=api.put("/api/workspace/generation-settings",headers=current,json={"scopes":{"ask":{"provider":"openai","model":"gpt-5.6-luna"},"discovery":{"provider":"openai","model":"gpt-5.6-luna"},"analysis":{"provider":"openai","model":"gpt-5.6-luna"}}})
            unavailable=api.put("/api/workspace/generation-settings",headers={**owner,"X-Workspace-ID":workspace["id"]},json={"scopes":{"ask":{"provider":"gemini","model":"gemini-3.5-flash-lite"},"discovery":{"provider":"openai","model":"gpt-5.6-luna"},"analysis":{"provider":"openai","model":"gpt-5.6-luna"}}})
    finally:
        main.app.dependency_overrides.clear()
    assert catalog.status_code==200
    assert any(item["provider"]=="gemini" and not item["available"] for item in catalog.json()["options"])
    assert blocked.status_code==403 and unavailable.status_code==422


def test_owner_can_save_all_generation_scopes_including_mind_map(tmp_path, monkeypatch):
    _setup(tmp_path); monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    try:
        with TestClient(main.app) as api:
            owner={"X-Dev-User":"owner"}
            workspace=api.post("/api/workspaces",headers=owner,json={"name":"shared"}).json()
            headers={**owner,"X-Workspace-ID":workspace["id"]}
            scopes={scope:{"provider":"openai","model":"gpt-5.6-luna"} for scope in ("ask","discovery","analysis","mind_map")}
            saved=api.put("/api/workspace/generation-settings",headers=headers,json={"scopes":scopes})
    finally:
        main.app.dependency_overrides.clear()
    assert saved.status_code==200
    assert saved.json()["scopes"]==scopes


def test_persisted_retired_model_is_migrated_for_all_scopes_without_touching_metadata(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'legacy-settings.db'}")
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    store=PaperStore(session_factory=factory)
    owner, _=store.ensure_user(Principal(issuer="dev", subject="owner"))
    workspace=store.create_workspace(owner.id, "legacy")
    legacy_scopes={
        "ask":{"provider":"openai","model":"gpt-5.4-nano"},
        "discovery":{"provider":"openai","model":"gpt-5.4-nano"},
        "analysis":{"provider":"openai","model":"gpt-5.4-nano"},
        "mind_map":{"provider":"openai","model":"gpt-5.4-nano"},
    }
    with factory.begin() as session:
        session.add(WorkspaceGenerationSettingsRecord(
            workspace_id=workspace.id, scopes=legacy_scopes, updated_by=owner.id,
            updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ))

    scopes, updated_at, updated_by=store.generation_settings(workspace.id)

    assert set(scopes)=={"ask","discovery","analysis","mind_map"}
    assert all(choice=={"provider":"openai","model":"gpt-5.6-luna"} for choice in scopes.values())
    assert resolve_workspace_generation(store, workspace.id, "mind_map", None, None).model=="gpt-5.6-luna"
    assert updated_by==owner.id and updated_at.startswith("2026-07-01")
    with factory() as session:
        persisted=session.get(WorkspaceGenerationSettingsRecord, workspace.id)
        assert persisted.scopes==scopes


def test_alembic_migrates_retired_generation_settings_without_rewriting_run_history(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend=Path(__file__).resolve().parents[1]
    database_url=f"sqlite:///{tmp_path / 'generation-settings-migration.db'}"
    config=Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260729_0032")
    engine=create_engine(database_url)
    legacy_scopes={scope:{"provider":"openai","model":"gpt-5.4-nano"} for scope in ("ask","discovery","analysis")}
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO users (id, issuer, subject, created_at) VALUES ('user', 'test', 'user', '2026-07-01 00:00:00')")
        connection.exec_driver_sql("INSERT INTO workspaces (id, name, created_by, is_personal, created_at) VALUES ('workspace', 'workspace', 'user', 0, '2026-07-01 00:00:00')")
        connection.exec_driver_sql("INSERT INTO workspace_generation_settings (workspace_id, scopes, updated_by, updated_at) VALUES ('workspace', :scopes, 'user', '2026-07-01 00:00:00')", {"scopes": json.dumps(legacy_scopes)})
        connection.exec_driver_sql("INSERT INTO generation_audits (id, workspace_id, created_by, scope, provider, model, outcome, prompt_version, usage, created_at) VALUES ('audit', 'workspace', 'user', 'ask', 'openai', 'gpt-5.4-nano', 'succeeded', '', '{}', '2026-07-01 00:00:00')")
        connection.exec_driver_sql("INSERT INTO research_runs (id, workspace_id, created_by, research_question, source_paper_ids, excluded_paper_ids, purpose, success_criteria, plan, model, generation_provider, prompt_version, status, cancel_requested, created_at) VALUES ('run', 'workspace', 'user', '', '[]', '[]', '', '', '{}', 'gpt-5.4-nano', 'openai', '', 'succeeded', 0, '2026-07-01 00:00:00')")

    command.upgrade(config, "head")

    with engine.connect() as connection:
        migrated=connection.exec_driver_sql("SELECT scopes, updated_by, updated_at FROM workspace_generation_settings WHERE workspace_id='workspace'").one()
        assert json.loads(migrated.scopes)=={
            scope:{"provider":"openai","model":"gpt-5.6-luna"}
            for scope in ("ask","discovery","analysis","mind_map")
        }
        assert migrated.updated_by=="user" and str(migrated.updated_at).startswith("2026-07-01")
        assert connection.exec_driver_sql("SELECT model FROM generation_audits WHERE id='audit'").scalar_one()=="gpt-5.4-nano"
        assert connection.exec_driver_sql("SELECT model, generation_provider FROM research_runs WHERE id='run'").one()==("gpt-5.4-nano", "openai")
