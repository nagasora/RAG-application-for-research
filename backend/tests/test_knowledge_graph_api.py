import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base
from app.models import Principal
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'graph.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: LocalOriginalStorage(tmp_path / "originals")
    return store


def _headers(user: str, workspace_id: str | None = None):
    values = {"X-Dev-User": user}
    if workspace_id:
        values["X-Workspace-ID"] = workspace_id
    return values


def test_graph_keeps_immutable_provenance_and_marks_downstream_nodes_for_review(tmp_path):
    _setup(tmp_path)
    source_content = "loss = objective(theta)"
    content_hash = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    try:
        with TestClient(main.app) as client:
            source = client.post("/api/graph/sources", headers=_headers("alice"), json={
                "kind": "python", "locator": "repo://experiment.py@abc123",
                "content_hash": content_hash, "content": source_content,
                "metadata": {"git_commit": "abc123"},
            })
            assert source.status_code == 201
            source_id = source.json()["id"]
            duplicate = client.post("/api/graph/sources", headers=_headers("alice"), json={
                "kind": "python", "locator": "repo://experiment.py@abc123",
                "content_hash": content_hash, "content": source_content,
            })
            assert duplicate.status_code == 201 and duplicate.json()["id"] == source_id

            span = client.post(f"/api/graph/sources/{source_id}/spans", headers=_headers("alice"), json={
                "source_version_id": source_id, "line_start": 10, "line_end": 14,
                "locator": {"ast_hash": "f" * 64}, "text": "loss = objective(theta)",
            })
            assert span.status_code == 201
            span_id = span.json()["id"]
            source_node = client.post("/api/graph/nodes", headers=_headers("alice"), json={
                "node_type": "source", "content": "Objective implementation",
                "evidence_span_ids": [span_id], "status": "active",
            })
            assert source_node.status_code == 201
            idea = client.post("/api/graph/nodes", headers=_headers("alice"), json={
                "node_type": "hypothesis", "content": "The objective is stable",
            })
            assert idea.status_code == 201 and idea.json()["status"] == "review_pending"
            edge = client.post("/api/graph/edges", headers=_headers("alice"), json={
                "source_node_id": source_node.json()["id"], "target_node_id": idea.json()["id"],
                "relation": "informs", "evidence_span_ids": [span_id],
                "metadata": {"confidence": 0.9},
            })
            assert edge.status_code == 201
            assert edge.json()["evidence"][0]["source_span_id"] == span_id
            self_edge = client.post("/api/graph/edges", headers=_headers("alice"), json={
                "source_node_id": source_node.json()["id"], "target_node_id": source_node.json()["id"],
                "relation": "informs", "evidence_span_ids": [span_id],
            })
            assert self_edge.status_code == 422
            snapshot = client.get("/api/graph", headers=_headers("alice"))
            assert snapshot.status_code == 200
            assert len(snapshot.json()["nodes"]) == 2 and len(snapshot.json()["edges"]) == 1

            retrieved = client.post("/api/graph/retrieve", headers=_headers("alice"), json={
                "seeds": [{"node_id": source_node.json()["id"], "relevance": 0.9}],
            })
            assert retrieved.status_code == 200
            assert {item["node"]["id"] for item in retrieved.json()} == {source_node.json()["id"], idea.json()["id"]}
            derived = next(item for item in retrieved.json() if item["node"]["id"] == idea.json()["id"])
            assert derived["hop_count"] == 1 and derived["hop_path"][0]["edge_id"] == edge.json()["id"]

            rejected = client.patch(
                f"/api/graph/nodes/{source_node.json()['id']}/status", headers=_headers("alice"),
                json={"status": "rejected"},
            )
            assert rejected.status_code == 200
            assert rejected.json()["affected_node_ids"] == [idea.json()["id"]]
            stale = client.get(f"/api/graph/nodes/{idea.json()['id']}", headers=_headers("alice"))
            assert stale.status_code == 200 and stale.json()["status"] == "review_required"
    finally:
        main.app.dependency_overrides.clear()


def test_graph_rejects_cross_workspace_anchor_and_viewer_writes(tmp_path):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            source = client.post("/api/graph/sources", headers=_headers("alice"), json={
                "kind": "markdown", "locator": "note://a",
                "content_hash": hashlib.sha256(b"research note").hexdigest(), "content": "research note",
            }).json()
            invalid_hash = client.post("/api/graph/sources", headers=_headers("alice"), json={
                "kind": "markdown", "locator": "note://invalid", "content_hash": "z" * 64,
                "content": "research note",
            })
            _, alice_workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
            bob, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
            store.add_workspace_member(alice_workspace.id, bob.id, "viewer")
            viewer_write = client.post("/api/graph/nodes", headers=_headers("bob", alice_workspace.id), json={
                "node_type": "idea", "content": "viewer write",
            })
            outsider_read = client.get(f"/api/graph/sources/{source['id']}/spans", headers=_headers("bob"))
        assert viewer_write.status_code == 403
        assert outsider_read.status_code == 404
        assert invalid_hash.status_code == 422
    finally:
        main.app.dependency_overrides.clear()


def test_paper_ingestion_seeds_immutable_page_anchors(tmp_path):
    _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            uploaded = client.post(
                "/api/papers/upload", headers=_headers("alice"),
                files={"files": ("theory.md", b"# Theorem\nGrounded claim", "text/markdown")},
            )
            assert uploaded.status_code == 200
            sources = client.get("/api/graph/sources", headers=_headers("alice"))
            assert sources.status_code == 200
            paper_source = next(item for item in sources.json() if item["kind"] == "paper")
            spans = client.get(f"/api/graph/sources/{paper_source['id']}/spans", headers=_headers("alice"))
            span_id = spans.json()[0]["id"]
            grounded = client.post("/api/graph/nodes", headers=_headers("alice"), json={
                "node_type": "source", "content": "Grounded theorem",
                "evidence_span_ids": [span_id], "status": "active",
            })
            deleted = client.delete(f"/api/papers/{uploaded.json()[0]['paper']['id']}", headers=_headers("alice"))
        assert len(spans.json()) == 1
        assert spans.json()[0]["page"] == 1
        assert "Grounded claim" in spans.json()[0]["text"]
        assert grounded.status_code == 201
        assert deleted.status_code == 409
    finally:
        main.app.dependency_overrides.clear()


def test_graph_endpoints_and_dtos_are_exposed_in_openapi():
    schema = main.app.openapi()
    assert "/api/graph" in schema["paths"]
    assert "/api/graph/sources" in schema["paths"]
    assert "/api/graph/sources/import" in schema["paths"]
    assert "/api/graph/retrieve" in schema["paths"]
    assert "GraphSnapshot" in schema["components"]["schemas"]
    assert "SourceSpanCreate" in schema["components"]["schemas"]


def test_graph_source_import_verifies_content_and_creates_typed_spans(tmp_path):
    _setup(tmp_path)
    content = "def objective(theta):\n    return theta ** 2\n"
    try:
        with TestClient(main.app) as client:
            response = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
                "kind": "python", "locator": "repo://objective.py@abc123", "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            })
            bad_hash = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
                "kind": "python", "locator": "repo://objective.py@bad", "content": content,
                "content_hash": "0" * 64,
            })
            retry = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
                "kind": "python", "locator": "repo://objective.py@abc123", "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            })
            alternate = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
                "kind": "markdown", "locator": "note://objective", "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            })
        assert response.status_code == 201
        assert response.json()["source"]["kind"] == "python"
        assert response.json()["spans"][0]["locator"]["ast_hash"]
        assert response.json()["spans"][0]["line_start"] == 1
        assert bad_hash.status_code == 422
        assert retry.status_code == 201 and retry.json()["source"]["id"] == response.json()["source"]["id"]
        assert alternate.status_code == 201
        assert alternate.json()["source"]["id"] != response.json()["source"]["id"]
        assert alternate.json()["source"]["kind"] == "markdown"
    finally:
        main.app.dependency_overrides.clear()
