from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app import main
from app.database import Base
from app.models import MindMapCreate
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mind-map.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    return store


def _headers(user="alice", workspace=None):
    values = {"X-Dev-User": user}
    if workspace: values["X-Workspace-ID"] = workspace
    return values


def test_manual_mind_map_is_workspace_scoped_and_root_is_protected(tmp_path):
    _setup(tmp_path)
    payload = {"title": "研究計画", "source_scope": {"mode": "manual"}, "nodes": [
        {"client_id": "r", "kind": "root", "title": "研究計画"},
        {"client_id": "c", "parent_client_id": "r", "kind": "theme", "title": "背景"},
    ]}
    try:
        with TestClient(main.app) as client:
            created = client.post("/api/mind-maps", headers=_headers(), json=payload)
            assert created.status_code == 201, created.text
            mapping = created.json(); root = next(node for node in mapping["nodes"] if node["parent_id"] is None)
            assert root["kind"] == "root" and root["order_index"] == 0
            assert client.delete(f"/api/mind-map-nodes/{root['id']}", headers=_headers()).status_code == 409
            assert client.get("/api/mind-maps", headers=_headers("bob")).json() == []
            child = next(node for node in mapping["nodes"] if node["parent_id"])
            assert client.delete(f"/api/mind-map-nodes/{child['id']}", headers=_headers()).status_code == 204
            assert len(client.get(f"/api/mind-maps/{mapping['id']}", headers=_headers()).json()["nodes"]) == 1
    finally:
        main.app.dependency_overrides.clear()


def test_mind_map_rejects_non_root_tree(tmp_path):
    _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            response = client.post("/api/mind-maps", headers=_headers(), json={"title": "bad", "nodes": [{"client_id": "x", "kind": "theme", "title": "x"}]})
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "invalid_mind_map"
    finally:
        main.app.dependency_overrides.clear()


def test_mind_map_limits_cross_map_parent_and_viewer_write(tmp_path):
    _setup(tmp_path)
    root = lambda title: {"title": title, "nodes": [{"client_id": "r", "kind": "root", "title": title}]}
    try:
        with TestClient(main.app) as client:
            too_many = root("too many")
            too_many["nodes"] = [
                {"client_id": f"n-{index}", "parent_client_id": None if index == 0 else "n-0",
                 "kind": "root" if index == 0 else "theme", "title": str(index)}
                for index in range(251)
            ]
            assert client.post("/api/mind-maps", headers=_headers(), json=too_many).status_code == 422

            deep_nodes = [{"client_id": "n-0", "kind": "root", "title": "root"}]
            deep_nodes.extend(
                {"client_id": f"n-{index}", "parent_client_id": f"n-{index - 1}", "kind": "theme", "title": str(index)}
                for index in range(1, 10)
            )
            assert client.post("/api/mind-maps", headers=_headers(), json={"title": "deep", "nodes": deep_nodes}).status_code == 422

            first = client.post("/api/mind-maps", headers=_headers(), json=root("first")).json()
            second = client.post("/api/mind-maps", headers=_headers(), json=root("second")).json()
            first_root = first["nodes"][0]["id"]
            second_root = second["nodes"][0]["id"]
            cross_parent = client.post(
                f"/api/mind-map-nodes/{first_root}/children",
                headers=_headers(),
                json={"nodes": [{"client_id": "x", "parent_client_id": second_root, "kind": "theme", "title": "cross"}]},
            )
            assert cross_parent.status_code == 422

            workspace = client.post("/api/workspaces", headers=_headers(), json={"name": "Shared"}).json()
            client.get("/api/me", headers=_headers("bob"))
            client.post(
                f"/api/workspaces/{workspace['id']}/members",
                headers=_headers(),
                json={"subject": "bob", "role": "viewer"},
            )
            viewer_headers = _headers("bob", workspace["id"])
            assert client.get("/api/mind-maps", headers=viewer_headers).status_code == 200
            assert client.post("/api/mind-maps", headers=viewer_headers, json=root("denied")).status_code == 403
    finally:
        main.app.dependency_overrides.clear()


def test_confirmations_are_idempotent_and_subtree_delete_preserves_outputs(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(
        main,
        "generate_candidate",
        lambda *args, **kwargs: SimpleNamespace(
            nodes=[{
                "client_id": "candidate-1",
                "parent_client_id": kwargs.get("parent_client_id"),
                "kind": "task",
                "title": "追試",
                "body": "独立データで検証する",
                "evidence_ref_ids": [],
                "source_span_ids": [],
                "generated": True,
            }],
            attempted=False,
            fallback_reason=None,
        ),
    )
    try:
        with TestClient(main.app) as client:
            mapping = client.post("/api/mind-maps", headers=_headers(), json={
                "title": "研究計画",
                "nodes": [
                    {"client_id": "r", "kind": "root", "title": "研究計画"},
                    {"client_id": "c", "parent_client_id": "r", "kind": "claim", "title": "中心主張", "body": "検証対象"},
                ],
            }).json()
            child = next(node for node in mapping["nodes"] if node["parent_id"])
            child_payload = {"nodes": [{
                "client_id": "stable-child-confirmation",
                "parent_client_id": child["id"],
                "kind": "question",
                "title": "追試条件",
            }]}
            children_first = client.post(
                f"/api/mind-map-nodes/{child['id']}/children",
                headers=_headers(), json=child_payload,
            )
            children_second = client.post(
                f"/api/mind-map-nodes/{child['id']}/children",
                headers=_headers(), json=child_payload,
            )
            assert children_first.status_code == children_second.status_code == 201
            assert children_first.json()[0]["id"] == children_second.json()[0]["id"]

            note = client.post(
                f"/api/mind-map-nodes/{child['id']}/notes",
                headers=_headers(),
                json={"title": "中心主張", "content": "検証対象", "origin_kind": "mind_map"},
            )
            assert note.status_code == 201, note.text

            generated = client.post(
                f"/api/mind-map-nodes/{child['id']}/research-actions/generate",
                headers=_headers(),
            )
            assert generated.status_code == 200, generated.text
            confirmation = {
                "research_run_id": generated.json()["research_run_id"],
                "actions": generated.json()["candidates"][:1],
            }
            first = client.post(
                f"/api/mind-map-nodes/{child['id']}/research-actions",
                headers=_headers(), json=confirmation,
            )
            second = client.post(
                f"/api/mind-map-nodes/{child['id']}/research-actions",
                headers=_headers(), json=confirmation,
            )
            assert first.status_code == second.status_code == 201
            assert first.json()[0]["id"] == second.json()[0]["id"]

            promoted = client.post(
                f"/api/mind-map-nodes/{child['id']}/graph-node",
                headers=_headers(), json={"node_type": "hypothesis"},
            )
            promoted_again = client.post(
                f"/api/mind-map-nodes/{child['id']}/graph-node",
                headers=_headers(), json={"node_type": "hypothesis"},
            )
            assert promoted.status_code == promoted_again.status_code == 201
            assert promoted.json()["id"] == promoted_again.json()["id"]

            assert client.delete(f"/api/mind-map-nodes/{child['id']}", headers=_headers()).status_code == 204
            actions = client.get("/api/research-actions", headers=_headers()).json()
            notes = client.get("/api/notes?origin_kind=mind_map", headers=_headers()).json()
            graph = client.get("/api/graph", headers=_headers()).json()
            assert len(actions) == 1 and actions[0]["mind_map_node_id"] is None
            assert len(notes) == 1 and notes[0]["mind_map_node_id"] is None
            assert any(node["id"] == promoted.json()["id"] for node in graph["nodes"])
    finally:
        main.app.dependency_overrides.clear()


def test_ai_map_rejects_evidence_outside_its_source_scope(tmp_path):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            workspace_id = client.get("/api/me", headers=_headers()).json()["personal_workspace"]["id"]
            source_a, spans_a = store.create_source_import(
                workspace_id,
                kind="note",
                locator="test:a",
                content_hash=sha256(b"a").hexdigest(),
                metadata={},
                spans=[{"page": 1, "text": "根拠A"}],
            )
            source_b, spans_b = store.create_source_import(
                workspace_id,
                kind="note",
                locator="test:b",
                content_hash=sha256(b"b").hexdigest(),
                metadata={},
                spans=[{"page": 1, "text": "根拠B"}],
            )
            del source_a, source_b
            scope = {"mode": "evidence", "evidence_ref_ids": [], "source_span_ids": [spans_a[0].id]}
            run = client.post("/api/research/runs", headers=_headers(), json={
                "source_paper_ids": [],
                "purpose": "mind_map_generation",
                "plan": {"mind_map_source_scope": scope},
            })
            assert run.status_code == 201, run.text
            confirmation = {
                "title": "AI map",
                "source_scope": scope,
                "generation_kind": "ai",
                "generation_run_id": run.json()["id"],
                "nodes": [{
                    "client_id": "root",
                    "kind": "root",
                    "title": "AI map",
                    "source_span_ids": [spans_a[0].id],
                    "generated": True,
                }],
            }
            mapping = client.post("/api/mind-maps", headers=_headers(), json=confirmation)
            assert mapping.status_code == 201, mapping.text
            retried = client.post("/api/mind-maps", headers=_headers(), json=confirmation)
            assert retried.status_code == 201
            assert retried.json()["id"] == mapping.json()["id"]
            node_id = mapping.json()["nodes"][0]["id"]
            outside = client.patch(
                f"/api/mind-map-nodes/{node_id}",
                headers=_headers(),
                json={"source_span_ids": [spans_b[0].id]},
            )
            assert outside.status_code == 422
            assert client.get(f"/api/mind-maps/{mapping.json()['id']}", headers=_headers()).json()["nodes"][0]["source_span_ids"] == [spans_a[0].id]
    finally:
        main.app.dependency_overrides.clear()


def test_concurrent_ai_map_confirmation_returns_the_same_map(tmp_path, monkeypatch):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            workspace_id = client.get("/api/me", headers=_headers()).json()["personal_workspace"]["id"]
            scope = {"mode": "evidence", "evidence_ref_ids": [], "source_span_ids": []}
            run = client.post("/api/research/runs", headers=_headers(), json={
                "source_paper_ids": [],
                "purpose": "mind_map_generation",
                "plan": {"mind_map_source_scope": scope},
            })
            assert run.status_code == 201, run.text

        confirmation = MindMapCreate.model_validate({
            "title": "Concurrent AI map",
            "source_scope": scope,
            "generation_kind": "ai",
            "generation_run_id": run.json()["id"],
            "nodes": [{
                "client_id": "root",
                "kind": "root",
                "title": "Concurrent AI map",
                "generated": True,
            }],
        })
        barrier = Barrier(2)
        original_validate = PaperStore._validate_mind_map_evidence

        def synchronize_after_existing_check(session, scoped_workspace_id, evidence_ref_ids, source_span_ids):
            barrier.wait(timeout=5)
            return original_validate(session, scoped_workspace_id, evidence_ref_ids, source_span_ids)

        monkeypatch.setattr(
            PaperStore,
            "_validate_mind_map_evidence",
            staticmethod(synchronize_after_existing_check),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _: store.create_mind_map(workspace_id, "alice", confirmation),
                range(2),
            ))

        assert results[0].id == results[1].id
        assert len(store.list_mind_maps(workspace_id)) == 1
    finally:
        main.app.dependency_overrides.clear()
