from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base
from app.models import Paper, Principal
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    return store


def _headers(user, workspace_id=None):
    headers = {"X-Dev-User": user}
    if workspace_id:
        headers["X-Workspace-ID"] = workspace_id
    return headers


def test_research_run_snapshots_context_artifacts_and_cancel(tmp_path):
    store = _setup(tmp_path)
    user, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
    paper = store.upsert(Paper(
        user_id="alice", workspace_id=workspace.id, created_by=user.id,
        title="Evidence", content_hash="d" * 64,
    ))
    try:
        with TestClient(main.app) as client:
            question = client.post("/api/research/questions", headers=_headers("alice"), json={"question": "What is reliable?"}).json()
            source_set = client.post("/api/source-sets", headers=_headers("alice"), json={"name": "Seed", "paper_ids": [paper.id]}).json()
            created = client.post("/api/research/runs", headers=_headers("alice"), json={
                "research_question_id": question["id"], "source_set_id": source_set["id"],
                "purpose": "Audit", "success_criteria": "Citations are traceable", "plan": {"steps": ["retrieve"]},
                "model": "gpt-5.4-nano", "prompt_version": "research-v1",
            })
            assert created.status_code == 201
            run = created.json()
            artifact = client.post(f"/api/research/runs/{run['id']}/artifacts", headers=_headers("alice"), json={
                "kind": "retrieval_candidates", "payload": {"candidates": [{"paper_id": paper.id, "rank": 1}]},
            })
            cancelled = client.post(f"/api/research/runs/{run['id']}/cancel", headers=_headers("alice"))
            detail = client.get(f"/api/research/runs/{run['id']}", headers=_headers("alice"))

        assert run["research_question"] == "What is reliable?" and run["source_paper_ids"] == [paper.id]
        assert artifact.status_code == 201 and artifact.json()["ordinal"] == 1
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert detail.json()["artifacts"][0]["payload"]["candidates"][0]["rank"] == 1
    finally:
        main.app.dependency_overrides.clear()


def test_research_run_is_workspace_scoped_and_viewers_cannot_write(tmp_path):
    store = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
    bob, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
    store.add_workspace_member(workspace.id, bob.id, "viewer")
    try:
        with TestClient(main.app) as client:
            run = client.post("/api/research/runs", headers=_headers("alice"), json={"purpose": "Read test"})
            run_id = run.json()["id"]
            listed = client.get("/api/research/runs", headers=_headers("bob", workspace.id))
            blocked = client.post(f"/api/research/runs/{run_id}/cancel", headers=_headers("bob", workspace.id))
            hidden = client.get(f"/api/research/runs/{run_id}", headers=_headers("bob"))
        assert listed.status_code == 200 and listed.json()[0]["id"] == run_id
        assert blocked.status_code == 403 and hidden.status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_research_run_graph_seed_is_workspace_scoped_and_canonicalized(tmp_path):
    _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            node = client.post("/api/graph/nodes", headers=_headers("alice"), json={
                "node_type": "idea", "content": "  Canonical\n graph   seed  " + "x" * 1_250,
            }).json()
            created = client.post("/api/research/runs", headers=_headers("alice"), json={
                "purpose": "graph seeded", "plan": {
                    "steps": ["retrieve"],
                    "graph_seed": {
                        "intent": "explore", "node_id": node["id"], "content": "forged client content",
                    },
                },
            })
            invalid_intent = client.post("/api/research/runs", headers=_headers("alice"), json={
                "plan": {"graph_seed": {"intent": "update", "node_id": node["id"]}},
            })
            missing = client.post("/api/research/runs", headers=_headers("alice"), json={
                "plan": {"graph_seed": {"intent": "design", "node_id": "missing-node"}},
            })
            foreign_node = client.post("/api/graph/nodes", headers=_headers("bob"), json={
                "node_type": "idea", "content": "Bob node",
            }).json()
            foreign = client.post("/api/research/runs", headers=_headers("alice"), json={
                "plan": {"graph_seed": {"intent": "challenge", "node_id": foreign_node["id"]}},
            })
            legacy_plan = client.post("/api/research/runs", headers=_headers("alice"), json={
                "plan": {"unrelated": {"free_form": True}},
            })

        assert created.status_code == 201
        seed = created.json()["plan"]["graph_seed"]
        assert seed["intent"] == "explore" and seed["node_id"] == node["id"]
        assert seed["content"] == ("Canonical graph seed " + "x" * 1_250)[:1200]
        assert created.json()["plan"]["steps"] == ["retrieve"]
        assert invalid_intent.status_code == 422
        assert missing.status_code == 422 and foreign.status_code == 422
        assert legacy_plan.status_code == 201
        assert legacy_plan.json()["plan"] == {"unrelated": {"free_form": True}}
    finally:
        main.app.dependency_overrides.clear()
