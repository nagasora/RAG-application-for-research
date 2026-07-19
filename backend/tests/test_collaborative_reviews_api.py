import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, EvidenceRefRecord, ResearchRunRecord
from app.models import Citation, Principal
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reviews.db'}",
        connect_args={"check_same_thread": False},
    )
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: LocalOriginalStorage(
        tmp_path / "originals",
    )
    return store


def _headers(user, workspace_id=None):
    result = {"X-Dev-User": user}
    if workspace_id:
        result["X-Workspace-ID"] = workspace_id
    return result


def test_review_threads_permissions_anchors_decisions_and_markdown_report(tmp_path):
    store = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
    bob, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
    viewer, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="viewer"))
    store.add_workspace_member(workspace.id, bob.id, "editor")
    store.add_workspace_member(workspace.id, viewer.id, "viewer")
    try:
        with TestClient(main.app) as client:
            content = "Exact negative evidence quote."
            imported = client.post(
                "/api/graph/sources/import", headers=_headers("alice"), json={
                    "kind": "markdown", "locator": "note://review-evidence",
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                },
            )
            span = imported.json()["spans"][0]
            node = client.post(
                "/api/graph/nodes", headers=_headers("alice"), json={
                    "node_type": "source", "content": "Negative finding",
                    "status": "active", "evidence_links": [{
                        "source_span_id": span["id"], "target_claim": "Claim under review",
                        "role": "contradicts", "extraction_quality": "high",
                        "quote_start": 0, "quote_end": len(span["text"]),
                        "verbatim_quote": span["text"],
                    }],
                },
            )
            evidence_link_id = node.json()["evidence"][0]["id"]
            created = client.post(
                "/api/reviews", headers=_headers("alice"), json={
                    "title": "Review contradictory evidence\n# forged heading",
                    "evidence_link_id": evidence_link_id, "assigned_to": bob.id,
                },
            )
            thread_id = created.json()["id"]
            commented = client.post(
                f"/api/reviews/{thread_id}/comments", headers=_headers("bob", workspace.id),
                json={"body": "The quote directly challenges the claim.\n<script>forged</script>"},
            )
            decided = client.post(
                f"/api/reviews/{thread_id}/decisions", headers=_headers("bob", workspace.id),
                json={"verdict": "rejected", "reason": "Contradicting primary evidence.\n## fake verdict"},
            )
            viewer_list = client.get("/api/reviews", headers=_headers("viewer", workspace.id))
            viewer_report = client.get("/api/reviews/report.md", headers=_headers("viewer", workspace.id))
            viewer_comment = client.post(
                f"/api/reviews/{thread_id}/comments", headers=_headers("viewer", workspace.id),
                json={"body": "viewer cannot write"},
            )
            viewer_decision = client.post(
                f"/api/reviews/{thread_id}/decisions", headers=_headers("viewer", workspace.id),
                json={"verdict": "accepted", "reason": "not allowed"},
            )

            run = client.post(
                "/api/research/runs", headers=_headers("alice"),
                json={"purpose": "claim review"},
            ).json()
            validation = client.post(
                f"/api/research/runs/{run['id']}/artifacts", headers=_headers("alice"),
                json={"kind": "validation", "payload": {"claims": [{
                    "claim_id": "claim-42", "text": "Validated claim text",
                    "classification": "evidence_backed", "citation_ids": [1],
                }]}},
            )
            candidates = client.get("/api/reviews/candidates", headers=_headers("alice"))
            claim_thread = client.post(
                "/api/reviews", headers=_headers("alice"), json={
                    "title": "Review generated claim", "research_run_id": run["id"],
                    "claim_id": "claim-42",
                },
            )
            unknown_claim = client.post(
                "/api/reviews", headers=_headers("alice"), json={
                    "title": "Unknown claim", "research_run_id": run["id"],
                    "claim_id": "missing-claim",
                },
            )
            client.post(
                f"/api/research/runs/{run['id']}/artifacts", headers=_headers("alice"),
                json={"kind": "validation", "payload": {"claims": [{
                    "claim_id": "claim-42", "text": "Duplicate claim text",
                    "classification": "unverified", "citation_ids": [],
                }]}},
            )
            ambiguous_claim = client.post(
                "/api/reviews", headers=_headers("alice"), json={
                    "title": "Ambiguous claim", "research_run_id": run["id"],
                    "claim_id": "claim-42",
                },
            )
            missing_anchor = client.post(
                "/api/reviews", headers=_headers("alice"), json={"title": "No anchor"},
            )
            both_anchors = client.post(
                "/api/reviews", headers=_headers("alice"), json={
                    "title": "Both anchors", "research_run_id": run["id"],
                    "claim_id": "claim-42", "evidence_link_id": evidence_link_id,
                },
            )
            invalid_assignment = client.patch(
                f"/api/reviews/{thread_id}/assignment", headers=_headers("alice"),
                json={"assigned_to": viewer.id},
            )
            complete_report = client.get(
                "/api/reviews/report.md", headers=_headers("viewer", workspace.id),
            )
            outsider_get = client.get(f"/api/reviews/{thread_id}", headers=_headers("bob"))
            foreign_anchor = client.post(
                "/api/reviews", headers=_headers("bob"), json={
                    "title": "Steal evidence", "evidence_link_id": evidence_link_id,
                },
            )

        assert imported.status_code == 201 and node.status_code == 201
        assert created.status_code == 201 and created.json()["assigned_to"] == bob.id
        assert commented.status_code == 200 and len(commented.json()["comments"]) == 1
        assert decided.status_code == 200 and decided.json()["status"] == "resolved"
        assert decided.json()["decisions"][0]["reason"].startswith("Contradicting primary evidence.")
        assert viewer_list.status_code == 200 and viewer_list.json()[0]["id"] == thread_id
        assert viewer_comment.status_code == 403 and viewer_decision.status_code == 403
        assert viewer_report.status_code == 200
        assert viewer_report.headers["content-type"].startswith("text/markdown")
        assert "Exact negative evidence quote." in viewer_report.text
        assert evidence_link_id in viewer_report.text
        assert "Contradicting primary evidence." in viewer_report.text
        assert "Claim under review" in viewer_report.text
        assert "\n# forged heading" not in viewer_report.text
        assert "<script>" not in viewer_report.text
        assert "\n## fake verdict" not in viewer_report.text
        assert validation.status_code == 201
        assert candidates.status_code == 200
        candidate = next(item for item in candidates.json() if item["claim_id"] == "claim-42")
        assert candidate["research_run_id"] == run["id"]
        assert candidate["claim_artifact_id"] == validation.json()["id"]
        assert candidate["text"] == "Validated claim text"
        assert candidate["citation_ids"] == [1]
        assert claim_thread.status_code == 201 and claim_thread.json()["claim_id"] == "claim-42"
        assert claim_thread.json()["claim_artifact_id"] == validation.json()["id"]
        assert claim_thread.json()["claim_snapshot"]["text"] == "Validated claim text"
        assert unknown_claim.status_code == 422 and ambiguous_claim.status_code == 422
        assert missing_anchor.status_code == 422 and both_anchors.status_code == 422
        assert invalid_assignment.status_code == 404
        assert "Validated claim text" in complete_report.text
        assert "evidence_backed" in complete_report.text
        assert outsider_get.status_code == 404 and foreign_anchor.status_code == 404
        with pytest.raises(IntegrityError):
            with store.session_factory.begin() as session:
                session.execute(delete(EvidenceRefRecord).where(EvidenceRefRecord.id == evidence_link_id))
        with pytest.raises(IntegrityError):
            with store.session_factory.begin() as session:
                session.execute(delete(ResearchRunRecord).where(ResearchRunRecord.id == run["id"]))
    finally:
        main.app.dependency_overrides.clear()


def test_graph_idea_candidates_keep_memory_when_available_and_offer_a_draft_for_fallback(tmp_path):
    store = _setup(tmp_path)
    _, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
    try:
        with TestClient(main.app) as client:
            fallback_conversation = client.post(
                "/api/research/conversations", headers=_headers("alice"), json={"title": "fallback"},
            ).json()
            store.add_research_exchange(
                workspace.id, fallback_conversation["id"], "質問", "ローカル抽出の回答", [], memory_delta={},
            )
            fallback_message_id = client.get(
                f"/api/research/conversations/{fallback_conversation['id']}", headers=_headers("alice"),
            ).json()["messages"][-1]["id"]
            fallback = client.get(
                f"/api/research/conversations/{fallback_conversation['id']}/messages/{fallback_message_id}/graph-candidates",
                headers=_headers("alice"),
            )

            memory_conversation = client.post(
                "/api/research/conversations", headers=_headers("alice"), json={"title": "memory"},
            ).json()
            store.add_research_exchange(
                workspace.id, memory_conversation["id"], "質問", "検証済み回答", [],
                memory_delta={"hypotheses": ["検証する仮説"]},
            )
            memory_message_id = client.get(
                f"/api/research/conversations/{memory_conversation['id']}", headers=_headers("alice"),
            ).json()["messages"][-1]["id"]
            memory = client.get(
                f"/api/research/conversations/{memory_conversation['id']}/messages/{memory_message_id}/graph-candidates",
                headers=_headers("alice"),
            )
        assert fallback.status_code == 200
        assert fallback.json()[0]["content"] == "ローカル抽出の回答"
        assert fallback.json()[0]["derived_from_memory"] is False
        assert fallback.json()[0]["kind"] == "hypothesis"
        assert memory.status_code == 200
        assert memory.json()[0]["content"] == "検証する仮説"
        assert memory.json()[0]["derived_from_memory"] is True
    finally:
        main.app.dependency_overrides.clear()


def test_atomic_conversation_graph_export_snapshots_citations_and_never_marks_chat_as_support(tmp_path):
    store = _setup(tmp_path)
    _, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
    try:
        with TestClient(main.app) as client:
            conversation = client.post(
                "/api/research/conversations", headers=_headers("alice"), json={"title": "export"},
            ).json()
            citation = Citation(**{
                "index": 1, "paper_id": "paper-1", "paper_title": "Paper title", "chunk_id": "chunk-1",
                "page": 3, "section": "Results", "excerpt": "quoted paper excerpt", "score": 0.9,
                "source_version_id": "version-1", "source_span_id": "span-1",
            })
            store.add_research_exchange(
                workspace.id, conversation["id"], "質問", "AIが生成した仮説", [citation], memory_delta={},
            )
            message_id = client.get(
                f"/api/research/conversations/{conversation['id']}", headers=_headers("alice"),
            ).json()["messages"][-1]["id"]
            source_text = '[{"role":"assistant","content":"AIが生成した仮説"}]'
            imported = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
                "kind": "chat", "locator": f"chat://conversation/{conversation['id']}/message/{message_id}",
                "content": source_text, "content_hash": hashlib.sha256(source_text.encode()).hexdigest(),
                "metadata": {"conversation_id": conversation["id"], "message_id": message_id},
            })
            assert imported.status_code == 201
            imported = imported.json()
            request = {
                "source_span_id": imported["spans"][0]["id"],
                "drafts": [{"candidate_id": "memory:one", "content": "AIが生成した仮説", "kind": "hypothesis", "derived_from_memory": False}],
            }
            created = client.post(
                f"/api/research/conversations/{conversation['id']}/messages/{message_id}/graph-drafts",
                headers=_headers("alice"), json=request,
            )
            retried = client.post(
                f"/api/research/conversations/{conversation['id']}/messages/{message_id}/graph-drafts",
                headers=_headers("alice"), json=request,
            )
            bad = client.post(
                f"/api/research/conversations/{conversation['id']}/messages/{message_id}/graph-drafts",
                headers=_headers("alice"), json={"source_span_id": "missing", "drafts": request["drafts"]},
            )
            forged_text = '[{"role":"assistant","content":"別の本文"}]'
            forged_source = client.post("/api/graph/sources/import", headers=_headers("alice"), json={
                "kind": "chat", "locator": f"chat://forged/{message_id}",
                "content": forged_text, "content_hash": hashlib.sha256(forged_text.encode()).hexdigest(),
                "metadata": {"conversation_id": conversation["id"], "message_id": message_id},
            }).json()
            forged = client.post(
                f"/api/research/conversations/{conversation['id']}/messages/{message_id}/graph-drafts",
                headers=_headers("alice"), json={"source_span_id": forged_source["spans"][0]["id"], "drafts": request["drafts"]},
            )
            generic_forged = client.post("/api/graph/nodes", headers=_headers("alice"), json={
                "node_type": "idea", "content": "偽装された会話案", "status": "review_pending",
                "metadata": {"origin": "research_conversation", "conversation_id": conversation["id"], "message_id": message_id},
                "evidence_span_ids": [forged_source["spans"][0]["id"]],
            })
        assert created.status_code == 201 and retried.status_code == 201
        assert created.json()[0]["id"] == retried.json()[0]["id"]
        node = created.json()[0]
        assert node["status"] == "review_pending"
        assert node["metadata"]["unverified"] is True
        snapshot = node["metadata"]["citation_snapshot"][0]
        assert {key: snapshot[key] for key in ("paper_id", "paper_title", "source_version_id", "source_span_id", "page", "excerpt")} == {
            "paper_id": "paper-1", "paper_title": "Paper title", "source_version_id": "version-1",
            "source_span_id": "span-1", "page": 3, "excerpt": "quoted paper excerpt",
        }
        assert node["evidence"][0]["role"] == "context"
        assert node["evidence"][0]["verbatim_quote"] == "AIが生成した仮説"
        assert bad.status_code == 404
        assert forged.status_code == 422
        assert generic_forged.status_code == 422
        assert len(store.list_knowledge_nodes(workspace.id)) == 1
    finally:
        main.app.dependency_overrides.clear()
