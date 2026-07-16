import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, EvidenceRefRecord, ResearchRunRecord
from app.models import Principal
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
