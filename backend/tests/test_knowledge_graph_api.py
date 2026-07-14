import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, KnowledgeEdgeStatusEventRecord
from app.models import Principal
from app.storage import LocalOriginalStorage
from app.store import PaperStore, ResourceConflictError


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


def _import_source(client: TestClient, content: str, locator: str = "note://evidence"):
    response = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
        "kind": "markdown", "locator": locator, "content": content,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    })
    assert response.status_code == 201
    assert response.json()["spans"]
    return response.json()


def test_graph_source_import_uses_writable_asset_namespace(tmp_path):
    """User-entered immutable sources must not require the paper-original mount."""
    _setup(tmp_path)
    content = "A durable research note"
    try:
        with TestClient(main.app) as client:
            imported = _import_source(client, content, "note://asset-backed")
        source = imported["source"]
        storage = main.app.dependency_overrides[main.get_original_storage]()
        storage_key = source["metadata"]["storage_key"]
        assert storage_key == f"assets/sources/{hashlib.sha256(content.encode('utf-8')).hexdigest()}/source.txt"
        assert storage.path_for(storage_key).read_bytes() == content.encode("utf-8")
    finally:
        main.app.dependency_overrides.clear()


def test_graph_keeps_immutable_provenance_and_marks_downstream_nodes_for_review(tmp_path):
    store = _setup(tmp_path)
    source_content = "loss = objective(theta)\n"
    content_hash = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    try:
        with TestClient(main.app) as client:
            source = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
                "kind": "python", "locator": "repo://experiment.py@abc123",
                "content_hash": content_hash, "content": source_content,
                "metadata": {"git_commit": "abc123"},
            })
            assert source.status_code == 201
            source_id = source.json()["source"]["id"]
            span_id = source.json()["spans"][0]["id"]
            duplicate = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
                "kind": "python", "locator": "repo://experiment.py@abc123",
                "content_hash": content_hash, "content": source_content,
            })
            assert duplicate.status_code == 201 and duplicate.json()["source"]["id"] == source_id
            fabricated = client.post(f"/api/graph/sources/{source_id}/spans", headers=_headers("alice"), json={
                "source_version_id": source_id, "text": "fabricated evidence",
            })
            assert fabricated.status_code == 405
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
            assert edge.json()["status"] == "active" and edge.json()["created_by"]
            invalid_relation = client.post("/api/graph/edges", headers=_headers("alice"), json={
                "source_node_id": source_node.json()["id"], "target_node_id": idea.json()["id"],
                "relation": "inform", "evidence_span_ids": [span_id],
            })
            assert invalid_relation.status_code == 422
            self_edge = client.post("/api/graph/edges", headers=_headers("alice"), json={
                "source_node_id": source_node.json()["id"], "target_node_id": source_node.json()["id"],
                "relation": "informs", "evidence_span_ids": [span_id],
            })
            assert self_edge.status_code == 422
            snapshot = client.get("/api/graph", headers=_headers("alice"))
            assert snapshot.status_code == 200
            assert len(snapshot.json()["nodes"]) == 2 and len(snapshot.json()["edges"]) == 1

            pending_retrieval = client.post("/api/graph/retrieve", headers=_headers("alice"), json={
                "seeds": [{"node_id": source_node.json()["id"], "relevance": 0.9}],
            })
            assert {item["node"]["id"] for item in pending_retrieval.json()} == {source_node.json()["id"]}
            activated = client.patch(
                f"/api/graph/nodes/{idea.json()['id']}/status", headers=_headers("alice"),
                json={"status": "active"},
            )
            assert activated.status_code == 200
            retrieved = client.post("/api/graph/retrieve", headers=_headers("alice"), json={
                "seeds": [{"node_id": source_node.json()["id"], "relevance": 0.9}],
            })
            assert retrieved.status_code == 200
            assert {item["node"]["id"] for item in retrieved.json()} == {source_node.json()["id"], idea.json()["id"]}
            derived = next(item for item in retrieved.json() if item["node"]["id"] == idea.json()["id"])
            assert derived["hop_count"] == 1 and derived["hop_path"][0]["edge_id"] == edge.json()["id"]

            edge_rejected = client.patch(
                f"/api/graph/edges/{edge.json()['id']}/status", headers=_headers("alice"),
                json={"status": "rejected", "reason": "relation was not supported by the selected evidence"},
            )
            assert edge_rejected.status_code == 200 and edge_rejected.json()["status"] == "rejected"
            without_edge = client.post("/api/graph/retrieve", headers=_headers("alice"), json={
                "seeds": [{"node_id": source_node.json()["id"], "relevance": 0.9}],
            })
            assert {item["node"]["id"] for item in without_edge.json()} == {source_node.json()["id"]}
            edge_reactivated = client.patch(
                f"/api/graph/edges/{edge.json()['id']}/status", headers=_headers("alice"),
                json={"status": "active", "reason": "relation was re-reviewed against the source"},
            )
            assert edge_reactivated.status_code == 200 and edge_reactivated.json()["status"] == "active"

            rejected = client.patch(
                f"/api/graph/nodes/{source_node.json()['id']}/status", headers=_headers("alice"),
                json={"status": "rejected"},
            )
            assert rejected.status_code == 200
            assert rejected.json()["affected_node_ids"] == [idea.json()["id"]]
            stale = client.get(f"/api/graph/nodes/{idea.json()['id']}", headers=_headers("alice"))
            assert stale.status_code == 200 and stale.json()["status"] == "review_required"
            excluded = client.post("/api/graph/retrieve", headers=_headers("alice"), json={
                "seeds": [{"node_id": source_node.json()["id"], "relevance": 0.9}],
            })
            assert excluded.status_code == 200 and excluded.json() == []
        with store.session_factory() as session:
            events = session.scalars(select(KnowledgeEdgeStatusEventRecord).where(
                KnowledgeEdgeStatusEventRecord.knowledge_edge_id == edge.json()["id"],
            ).order_by(KnowledgeEdgeStatusEventRecord.created_at)).all()
            assert [(event.from_status, event.to_status) for event in events] == [
                ("active", "rejected"), ("rejected", "active"),
            ]
    finally:
        main.app.dependency_overrides.clear()


def test_graph_rejects_cross_workspace_anchor_and_viewer_writes(tmp_path):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            source = _import_source(client, "research note", "note://a")["source"]
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


def test_reasoning_runs_reject_direct_and_transitive_provenance_cycles(tmp_path):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            node_ids = []
            for content in ("premise", "derived model", "derived hypothesis"):
                response = client.post("/api/graph/nodes", headers=_headers("alice"), json={
                    "node_type": "hypothesis", "content": content, "status": "active",
                })
                assert response.status_code == 201
                node_ids.append(response.json()["id"])

            direct_cycle = client.post("/api/graph/runs", headers=_headers("alice"), json={
                "operator": "hypothesis_generation",
                "input_node_ids": [node_ids[0]], "output_node_ids": [node_ids[0]],
            })
            assert direct_cycle.status_code == 422

            first = client.post("/api/graph/runs", headers=_headers("alice"), json={
                "operator": "abstraction",
                "input_node_ids": [node_ids[0]], "output_node_ids": [node_ids[1]],
            })
            second = client.post("/api/graph/runs", headers=_headers("alice"), json={
                "operator": "formulation",
                "input_node_ids": [node_ids[1]], "output_node_ids": [node_ids[2]],
            })
            assert first.status_code == 201 and second.status_code == 201

            _, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
            with pytest.raises(ValueError, match="disjoint"):
                store.update_reasoning_run(
                    workspace.id, first.json()["id"], output_node_ids=[node_ids[0]],
                )

            transitive_cycle = client.post("/api/graph/runs", headers=_headers("alice"), json={
                "operator": "backward_self_support",
                "input_node_ids": [node_ids[2]], "output_node_ids": [node_ids[0]],
            })
            assert transitive_cycle.status_code == 422
            assert "provenance cycle" in transitive_cycle.json()["detail"]
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


def test_deleting_an_ungrounded_paper_removes_its_source_spans_and_rejects_manual_paper_source(tmp_path):
    _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            _import_source(client, "temporary evidence", "note://same-bytes-before-paper")
            uploaded = client.post(
                "/api/papers/upload", headers=_headers("alice"),
                files={"files": ("temporary.md", b"temporary evidence", "text/markdown")},
            )
            paper = uploaded.json()[0]["paper"]
            sources = client.get("/api/graph/sources", headers=_headers("alice")).json()
            paper_source = next(item for item in sources if item["paper_id"] == paper["id"])
            assert paper_source["kind"] == "paper"
            forged = client.post("/api/graph/sources", headers=_headers("alice"), json={
                "kind": "paper", "locator": "paper://forged", "paper_id": paper["id"],
                "content_hash": "0" * 64,
            })
            deleted = client.delete(f"/api/papers/{paper['id']}", headers=_headers("alice"))
            missing_spans = client.get(
                f"/api/graph/sources/{paper_source['id']}/spans", headers=_headers("alice"),
            )
        assert forged.status_code == 422
        assert deleted.status_code == 204
        assert missing_spans.status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_status_cascade_follows_dependency_semantics_only(tmp_path):
    _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            span_id = _import_source(client, "grounded relation")["spans"][0]["id"]
            nodes = []
            for content in ("premise", "contradicted claim", "dependent implementation", "extended theory"):
                response = client.post("/api/graph/nodes", headers=_headers("alice"), json={
                    "node_type": "hypothesis", "content": content, "status": "active",
                })
                assert response.status_code == 201
                nodes.append(response.json())
            contradiction = client.post("/api/graph/edges", headers=_headers("alice"), json={
                "source_node_id": nodes[0]["id"], "target_node_id": nodes[1]["id"],
                "relation": "contradicts", "evidence_span_ids": [span_id],
            })
            reverse_dependency = client.post("/api/graph/edges", headers=_headers("alice"), json={
                "source_node_id": nodes[2]["id"], "target_node_id": nodes[0]["id"],
                "relation": "depends_on", "evidence_span_ids": [span_id],
            })
            extension = client.post("/api/graph/edges", headers=_headers("alice"), json={
                "source_node_id": nodes[3]["id"], "target_node_id": nodes[0]["id"],
                "relation": "extends", "evidence_span_ids": [span_id],
            })
            assert contradiction.status_code == 201
            assert reverse_dependency.status_code == 201 and extension.status_code == 201
            disabled_dependency = client.patch(
                f"/api/graph/edges/{reverse_dependency.json()['id']}/status",
                headers=_headers("alice"),
                json={"status": "rejected", "reason": "dependency relation was disproven"},
            )
            assert disabled_dependency.status_code == 200
            rejected = client.patch(
                f"/api/graph/nodes/{nodes[0]['id']}/status", headers=_headers("alice"),
                json={"status": "rejected"},
            )
            contradicted = client.get(f"/api/graph/nodes/{nodes[1]['id']}", headers=_headers("alice"))
            dependent = client.get(f"/api/graph/nodes/{nodes[2]['id']}", headers=_headers("alice"))
        assert rejected.json()["affected_node_ids"] == [nodes[3]["id"]]
        assert contradicted.json()["status"] == "active"
        assert dependent.json()["status"] == "active"
    finally:
        main.app.dependency_overrides.clear()


def test_graph_endpoints_and_dtos_are_exposed_in_openapi():
    schema = main.app.openapi()
    assert "/api/graph" in schema["paths"]
    assert "/api/graph/sources" in schema["paths"]
    assert "/api/graph/sources/import" in schema["paths"]
    assert "/api/graph/retrieve" in schema["paths"]
    assert "GraphSnapshot" in schema["components"]["schemas"]
    assert "post" not in schema["paths"]["/api/graph/sources/{source_version_id}/spans"]
    assert "SourceSpanCreate" not in schema["components"]["schemas"]


def test_graph_source_import_verifies_content_and_creates_typed_spans(tmp_path):
    store = _setup(tmp_path)
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
        _, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
        with pytest.raises(ResourceConflictError):
            store.create_source_import(
                workspace.id, kind="python", locator="repo://objective.py@abc123",
                content_hash=hashlib.sha256(content.encode()).hexdigest(), metadata={},
                spans=[{"line_start": 1, "line_end": 2, "locator": {"kind": "function"}, "text": "different"}],
            )
    finally:
        main.app.dependency_overrides.clear()
