from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, HypothesisCardRecord, IdeaRecord
from app.models import Principal
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ideas-experiments.db'}",
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


def _experiment_payload(hypothesis_card_id=None):
    return {
        "hypothesis_card_id": hypothesis_card_id,
        "intervention": "dose", "measurement": "score", "controls": "placebo",
        "confounders": ["baseline"], "predictions": ["score increases"],
        "decision_threshold": "p < 0.05", "stopping_rule": "n = 100",
        "required_data": "trial outcomes", "cost": "medium",
        "competing_hypothesis_discrimination": "placebo-adjusted effect",
        "evidence": ["registered protocol"],
    }


def test_idea_patch_promotion_snapshot_and_deleted_anchors(tmp_path):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            uploaded = client.post(
                "/api/papers/upload", headers=_headers("alice"),
                files={"files": ("anchor.txt", b"anchored evidence", "text/plain")},
            ).json()[0]["paper"]
            alice, workspace = store.ensure_user(
                Principal(issuer="paperpilot-dev", subject="alice"),
            )
            version = next(
                item for item in store.list_source_versions(workspace.id, "paper")
                if item.paper_id == uploaded["id"]
            )
            span = store.list_source_spans(workspace.id, version.id)[0]
            run = client.post(
                "/api/research/runs", headers=_headers("alice"),
                json={"purpose": "capture idea provenance"},
            ).json()
            validation = client.post(
                f"/api/research/runs/{run['id']}/artifacts", headers=_headers("alice"),
                json={"kind": "validation", "payload": {"claims": [{
                    "claim_id": "claim-1", "text": "Anchored claim",
                    "classification": "evidence_backed", "citation_ids": [1],
                }]}},
            )
            created = client.post(
                "/api/ideas", headers=_headers("alice"), json={
                    "kind": "observation", "content": "Initial anchored observation",
                    "research_run_id": run["id"], "claim_id": "claim-1",
                    "paper_id": uploaded["id"], "source_span_id": span.id,
                    "checklist": {"evidence": True},
                },
            )
            idea_id = created.json()["id"]
            incomplete = client.post(f"/api/ideas/{idea_id}/promote", headers=_headers("alice"))

            bob_upload = client.post(
                "/api/papers/upload", headers=_headers("bob"),
                files={"files": ("foreign.txt", b"foreign evidence", "text/plain")},
            ).json()[0]["paper"]
            invalid_anchor = client.patch(
                f"/api/ideas/{idea_id}", headers=_headers("alice"),
                json={"paper_id": bob_upload["id"], "content": "must roll back"},
            )
            unchanged = next(
                item for item in client.get("/api/ideas", headers=_headers("alice")).json()
                if item["id"] == idea_id
            )
            updated = client.patch(
                f"/api/ideas/{idea_id}", headers=_headers("alice"), json={
                    "kind": "hypothesis", "content": "Anchored hypothesis",
                    "checklist": {"evidence": True, "falsifier": True, "test": True},
                },
            )
            promoted = client.post(f"/api/ideas/{idea_id}/promote", headers=_headers("alice"))
            second_promotion = client.post(f"/api/ideas/{idea_id}/promote", headers=_headers("alice"))
            immutable = client.patch(
                f"/api/ideas/{idea_id}", headers=_headers("alice"),
                json={"content": "rewrite after promotion"},
            )
            card = next(
                item for item in client.get("/api/hypotheses", headers=_headers("alice")).json()
                if item["id"] == promoted.json()["hypothesis_card_id"]
            )
            deleted = client.delete(f"/api/papers/{uploaded['id']}", headers=_headers("alice"))
            after_delete = next(
                item for item in client.get("/api/ideas", headers=_headers("alice")).json()
                if item["id"] == idea_id
            )
            card_after_delete = client.get(
                f"/api/hypotheses/{card['id']}", headers=_headers("alice"),
            )

        assert created.status_code == 201
        assert validation.status_code == 201
        assert incomplete.status_code == 422
        assert invalid_anchor.status_code == 404
        assert unchanged["content"] == "Initial anchored observation"
        assert unchanged["paper_id"] == uploaded["id"]
        assert updated.status_code == 200 and updated.json()["kind"] == "hypothesis"
        assert promoted.status_code == 200 and promoted.json()["status"] == "promoted"
        assert second_promotion.status_code == 422 and immutable.status_code == 422
        snapshot = card["metadata"]["idea_anchor_snapshot"]
        assert snapshot["idea_id"] == idea_id
        assert snapshot["research_run_id"] == run["id"]
        assert snapshot["claim_id"] == "claim-1"
        assert snapshot["paper_id"] == uploaded["id"]
        assert snapshot["source_span_id"] == span.id
        assert deleted.status_code == 204
        assert after_delete["paper_id"] is None and after_delete["source_span_id"] is None
        assert card_after_delete.status_code == 200
        retained = card_after_delete.json()["metadata"]["idea_anchor_snapshot"]
        assert retained["paper"] == {
            "id": uploaded["id"], "title": "anchor",
            "content_hash": version.content_hash, "year": None,
        }
        assert retained["source"]["source_version"]["id"] == version.id
        assert retained["source"]["source_version"]["locator"] == version.locator
        assert retained["source"]["source_version"]["content_hash"] == version.content_hash
        assert retained["source"]["span"]["id"] == span.id
        assert retained["source"]["span"]["page"] == 1
        assert retained["source"]["span"]["verbatim_quote"] == "anchored evidence"
        assert retained["research_run"]["purpose"] == "capture idea provenance"
    finally:
        main.app.dependency_overrides.clear()


def test_claim_anchored_ideas_require_one_validation_claim_and_are_idempotent(tmp_path):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            run = client.post(
                "/api/research/runs", headers=_headers("alice"),
                json={"purpose": "claim anchor"},
            ).json()
            artifact = client.post(
                f"/api/research/runs/{run['id']}/artifacts", headers=_headers("alice"),
                json={"kind": "validation", "payload": {"claims": [{
                    "claim_id": "claim-anchored", "text": "Validated idea anchor",
                    "classification": "hypothesis", "citation_ids": [],
                }]}},
            )
            payload = {
                "content": "Capture validated claim", "research_run_id": run["id"],
                "claim_id": "claim-anchored", "checklist": {"evidence": True},
            }
            created = client.post("/api/ideas", headers=_headers("alice"), json=payload)
            retried = client.post("/api/ideas", headers=_headers("alice"), json={
                **payload, "content": "A retry must not create a second Idea",
            })
            unknown = client.post("/api/ideas", headers=_headers("alice"), json={
                **payload, "claim_id": "missing-claim",
            })
            no_run = client.post("/api/ideas", headers=_headers("alice"), json={
                "content": "Missing run", "claim_id": "claim-anchored",
            })
            other_run = client.post(
                "/api/research/runs", headers=_headers("alice"), json={"purpose": "other run"},
            ).json()
            wrong_run = client.post("/api/ideas", headers=_headers("alice"), json={
                **payload, "research_run_id": other_run["id"],
            })
            foreign_run = client.post(
                "/api/research/runs", headers=_headers("bob"), json={"purpose": "foreign run"},
            ).json()
            foreign_anchor = client.post("/api/ideas", headers=_headers("alice"), json={
                **payload, "research_run_id": foreign_run["id"],
            })
            duplicate_artifact = client.post(
                f"/api/research/runs/{run['id']}/artifacts", headers=_headers("alice"),
                json={"kind": "validation", "payload": {"claims": [{
                    "claim_id": "claim-anchored", "text": "Ambiguous duplicate",
                    "classification": "unverified", "citation_ids": [],
                }]}},
            )
            ambiguous = client.post("/api/ideas", headers=_headers("alice"), json=payload)
            free_first = client.post("/api/ideas", headers=_headers("alice"), json={"content": "free"})
            free_second = client.post("/api/ideas", headers=_headers("alice"), json={"content": "free"})
            patch_missing_run = client.patch(
                f"/api/ideas/{free_first.json()['id']}", headers=_headers("alice"),
                json={"claim_id": "claim-anchored"},
            )

        assert artifact.status_code == 201
        assert created.status_code == 201 and retried.status_code == 201
        assert created.json()["id"] == retried.json()["id"]
        assert created.json()["content"] == "Capture validated claim"
        assert unknown.status_code == 422 and no_run.status_code == 422 and wrong_run.status_code == 422
        assert foreign_anchor.status_code == 422
        assert duplicate_artifact.status_code == 201 and ambiguous.status_code == 422
        assert free_first.status_code == 201 and free_second.status_code == 201
        assert free_first.json()["id"] != free_second.json()["id"]
        assert patch_missing_run.status_code == 422
        with store.session_factory() as session:
            record = session.get(IdeaRecord, created.json()["id"])
            assert record.claim_artifact_id == artifact.json()["id"]
            assert record.claim_snapshot == {
                "claim_id": "claim-anchored", "text": "Validated idea anchor",
                "classification": "hypothesis", "citation_ids": [],
            }
            assert record.idempotency_key
    finally:
        main.app.dependency_overrides.clear()


def test_idea_actions_preserve_anchors_are_idempotent_and_require_human_review(tmp_path):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            run = client.post("/api/research/runs", headers=_headers("alice"), json={"purpose": "action origin"}).json()
            client.post(
                f"/api/research/runs/{run['id']}/artifacts", headers=_headers("alice"),
                json={"kind": "validation", "payload": {"claims": [{
                    "claim_id": "action-claim", "text": "A claim to decompose", "classification": "hypothesis", "citation_ids": [],
                }]}},
            )
            idea = client.post("/api/ideas", headers=_headers("alice"), json={
                "content": "Explore the proposed mechanism", "research_run_id": run["id"], "claim_id": "action-claim",
            }).json()
            decomposed = client.post(f"/api/ideas/{idea['id']}/actions/decompose", headers=_headers("alice"))
            retried = client.post(f"/api/ideas/{idea['id']}/actions/decompose", headers=_headers("alice"))
            action = decomposed.json()[0]
            reviewed = client.patch(f"/api/research-actions/{action['id']}", headers=_headers("alice"), json={
                "status": "in_progress", "due_date": "2026-08-01", "human_decision": "held", "human_reason": "Need a stronger control",
            })
            bob_idea = client.post("/api/ideas", headers=_headers("bob"), json={"content": "foreign"}).json()
            foreign = client.post("/api/research-actions", headers=_headers("alice"), json={
                "title": "must reject foreign origin", "idea_id": bob_idea["id"],
            })

        assert decomposed.status_code == 201 and len(decomposed.json()) == 3
        assert retried.status_code == 201
        assert {item["id"] for item in decomposed.json()} == {item["id"] for item in retried.json()}
        for item in decomposed.json():
            assert item["idea_id"] == idea["id"]
            assert item["research_run_id"] == run["id"]
            assert item["claim_id"] == "action-claim"
            assert item["claim_snapshot"]["text"] == "A claim to decompose"
            assert item["generation_class"] == "inference"
            assert item["human_decision"] == "unreviewed"
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "in_progress"
        assert reviewed.json()["due_date"] == "2026-08-01"
        assert reviewed.json()["human_decision"] == "held"
        assert foreign.status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_experiment_read_append_only_snapshot_and_workspace_scope(tmp_path):
    store = _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            alice_card = client.post(
                "/api/hypotheses", headers=_headers("alice"),
                json={"claim": "Dose improves score"},
            ).json()
            bob_card = client.post(
                "/api/hypotheses", headers=_headers("bob"),
                json={"claim": "Foreign hypothesis"},
            ).json()
            cross_hypothesis = client.post(
                "/api/experiments", headers=_headers("alice"),
                json=_experiment_payload(bob_card["id"]),
            )
            created = client.post(
                "/api/experiments", headers=_headers("alice"),
                json=_experiment_payload(alice_card["id"]),
            )
            plan_id = created.json()["id"]
            listed = client.get("/api/experiments", headers=_headers("alice"))
            detail = client.get(f"/api/experiments/{plan_id}", headers=_headers("alice"))
            first = client.post(
                f"/api/experiments/{plan_id}/results", headers=_headers("alice"),
                json={"outcome": "score +2", "data_snapshot": {"n": 50}, "interpretation": "promising"},
            )
            first_result = dict(first.json()["results"][0])
            second = client.post(
                f"/api/experiments/{plan_id}/results", headers=_headers("alice"),
                json={"outcome": "score +1", "data_snapshot": {"n": 100}, "interpretation": "attenuated"},
            )
            snapshot = client.get(
                f"/api/experiments/{plan_id}/snapshot", headers=_headers("alice"),
            )
            outsider = client.get(f"/api/experiments/{plan_id}", headers=_headers("bob"))
            with store.session_factory.begin() as session:
                session.delete(session.get(HypothesisCardRecord, alice_card["id"]))
            after_hypothesis_delete = client.get(
                f"/api/experiments/{plan_id}", headers=_headers("alice"),
            )

        assert cross_hypothesis.status_code == 404
        assert created.status_code == 201
        assert listed.status_code == 200 and [item["id"] for item in listed.json()] == [plan_id]
        assert detail.status_code == 200 and detail.json()["hypothesis_card_id"] == alice_card["id"]
        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["results"][0] == first_result
        assert len(second.json()["results"]) == 2
        assert len({item["id"] for item in second.json()["results"]}) == 2
        assert [event["action"] for event in second.json()["history"]] == [
            "created", "result_recorded", "result_recorded",
        ]
        assert len({event["event_id"] for event in second.json()["history"]}) == 3
        assert snapshot.status_code == 200
        assert snapshot.json()["schema_version"] == "paperpilot.experiment-plan.v1"
        assert snapshot.json()["experiment"]["results"] == second.json()["results"]
        assert outsider.status_code == 404
        assert after_hypothesis_delete.status_code == 200
        assert after_hypothesis_delete.json()["hypothesis_card_id"] is None
        assert after_hypothesis_delete.json()["hypothesis_snapshot"]["id"] == alice_card["id"]
        assert after_hypothesis_delete.json()["hypothesis_snapshot"]["claim"] == "Dose improves score"
    finally:
        main.app.dependency_overrides.clear()


def test_idea_rejects_whitespace_null_and_oversize_claim_id(tmp_path):
    _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            whitespace = client.post(
                "/api/ideas", headers=_headers("alice"),
                json={"content": "   "},
            )
            oversized = client.post(
                "/api/ideas", headers=_headers("alice"),
                json={"content": "valid", "claim_id": "x" * 129},
            )
            valid = client.post(
                "/api/ideas", headers=_headers("alice"),
                json={"content": "  trimmed idea  "},
            )
            null_content = client.patch(
                f"/api/ideas/{valid.json()['id']}", headers=_headers("alice"),
                json={"content": None},
            )
            null_kind = client.patch(
                f"/api/ideas/{valid.json()['id']}", headers=_headers("alice"),
                json={"kind": None},
            )
        assert whitespace.status_code == 422 and oversized.status_code == 422
        assert valid.status_code == 201 and valid.json()["content"] == "trimmed idea"
        assert null_content.status_code == 422 and null_kind.status_code == 422
    finally:
        main.app.dependency_overrides.clear()
