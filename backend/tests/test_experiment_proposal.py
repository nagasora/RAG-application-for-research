from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base
from app.experiment_analysis import analyze_full_text_paper, apply_verified_proposals, profile_snapshot
from app.experiment_proposal import ProposalGeneration, generate_experiment_proposals
from app.generation_settings import GenerationSelection
from app.models import Paper
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def test_openai_proposal_is_parsed_and_only_literal_quote_is_accepted(monkeypatch):
    class Adapter:
        def call(self, **kwargs):
            assert kwargs["operation"] == "responses.create.experiment_proposals"
            return SimpleNamespace(output_text='{"proposals":[{"category":"conditions","page":1,"quote":"The trial used five random seeds.","comparator":null}]}', usage={"input_tokens": 12, "output_tokens": 9})

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("app.experiment_proposal.get_openai_adapter", lambda: Adapter())
    selection = GenerationSelection("openai", "gpt-5.6-luna")
    result = generate_experiment_proposals(selection, {1: "Results. The trial used five random seeds."})
    assert result.succeeded and result.usage == {"input_tokens": 12, "output_tokens": 9}
    profile = analyze_full_text_paper(Paper(user_id="u", workspace_id="w", created_by="u", title="P"), {1: "Results. The trial used five random seeds."}, [], source_version_id="source", model="m", prompt_version="p")
    verified, accepted, rejected = apply_verified_proposals(profile, result.proposals, {1: "Results. The trial used five random seeds."}, [])
    assert accepted == 1 and rejected == 0
    assert verified.conditions[0].text == "The trial used five random seeds."


def test_mismatched_proposal_is_rejected_without_changing_deterministic_profile():
    page = "Results. Accuracy was 0.72."
    profile = analyze_full_text_paper(Paper(user_id="u", workspace_id="w", created_by="u", title="P"), {1: page}, [], source_version_id="source", model="m", prompt_version="p")
    verified, accepted, rejected = apply_verified_proposals(profile, [{"category": "observations", "page": 1, "quote": "Accuracy was 0.99.", "comparator": None}], {1: page}, [])
    assert accepted == 0 and rejected == 1 and verified == profile


def test_keyless_provider_never_calls_another_paid_provider(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = generate_experiment_proposals(GenerationSelection("gemini", "gemini-3.5-flash-lite"), {1: "Results. Accuracy was 0.72."})
    assert not result.succeeded and result.failure_code == "api_key_missing"


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'experiment-proposals.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: LocalOriginalStorage(tmp_path / "originals")
    return store


def _upload_pair(client):
    headers = {"X-Dev-User": "alice"}
    first = client.post("/api/papers/upload", headers=headers, files={"files": ("one.txt", b"Results. The trial used five random seeds. Accuracy was 0.72.", "text/plain")}).json()[0]["paper"]
    second = client.post("/api/papers/upload", headers=headers, files={"files": ("two.txt", b"Results. The trial used five random seeds. Accuracy was 0.81.", "text/plain")}).json()[0]["paper"]
    return headers, [first["id"], second["id"]]


def test_compare_cache_hit_does_not_call_model_again_and_keeps_rows(tmp_path, monkeypatch):
    store = _setup(tmp_path)
    calls = []

    def proposal(selection, pages):
        calls.append((selection.provider, selection.model))
        return ProposalGeneration(selection.provider, selection.model, "experiment-analysis-v2", ({"category": "conditions", "page": 1, "quote": "The trial used five random seeds.", "comparator": None},), {"input_tokens": 3}, True)

    monkeypatch.setattr(main, "generate_experiment_proposals", proposal)
    try:
        with TestClient(main.app) as client:
            headers, ids = _upload_pair(client)
            first = client.post("/api/analysis/experiments/compare", headers=headers, json={"paper_ids": ids})
            second = client.post("/api/analysis/experiments/compare", headers=headers, json={"paper_ids": ids})
        assert first.status_code == 200 and len(first.json()["matrix"]) == 2
        assert second.status_code == 200 and len(second.json()["matrix"]) == 2
        assert [item["status"] for item in second.json()["profiles"]] == ["cached", "cached"]
        assert all(item["generation_provider"] == "openai" for item in second.json()["profiles"])
        assert all(item["generation_usage"] == {"input_tokens": 3} for item in second.json()["profiles"])
        assert all(item["extraction_mode"] == "llm_verified" for item in second.json()["profiles"])
        assert len(calls) == 2
    finally:
        main.app.dependency_overrides.clear()


def test_compare_provider_failure_records_local_fallback_snapshot(tmp_path, monkeypatch):
    store = _setup(tmp_path)
    monkeypatch.setattr(main, "generate_experiment_proposals", lambda selection, pages: ProposalGeneration(selection.provider, selection.model, "experiment-analysis-v2", (), {}, False, "timeout"))
    try:
        with TestClient(main.app) as client:
            headers, ids = _upload_pair(client)
            response = client.post("/api/analysis/experiments/compare", headers=headers, json={"paper_ids": ids})
        assert response.status_code == 200
        assert all(item["generation_provider"] == "local" for item in response.json()["profiles"])
        assert all(item["extraction_mode"] == "deterministic_local" for item in response.json()["profiles"])
        paper = store.get_owned(store.ensure_user(main.Principal(issuer="paperpilot-dev", subject="alice"))[1].id, ids[0])
        source_version_id = store.paper_source_version_id(paper.workspace_id, paper.id)
        snapshot = store.get_experiment_profile_snapshot(paper.workspace_id, paper.id, source_version_id=source_version_id, content_hash=paper.content_hash, provider="local", model="evidence-extractor-v1", prompt_version="experiment-analysis-v1", cache_key=main.cache_key_for(paper.content_hash, "evidence-extractor-v1", "experiment-analysis-v1"))
        assert snapshot and snapshot["extraction_mode"] == "deterministic_local"
    finally:
        main.app.dependency_overrides.clear()


def test_saved_experiment_comparison_snapshots_evidence_and_model_without_new_generation(tmp_path, monkeypatch):
    store = _setup(tmp_path)
    calls = []

    def proposal(selection, pages):
        calls.append((selection.provider, selection.model))
        return ProposalGeneration(
            selection.provider,
            selection.model,
            "experiment-analysis-v2",
            ({"category": "conditions", "page": 1, "quote": "The trial used five random seeds.", "comparator": None},),
            {"input_tokens": 3, "output_tokens": 2},
            True,
        )

    monkeypatch.setattr(main, "generate_experiment_proposals", proposal)
    try:
        with TestClient(main.app) as client:
            headers, ids = _upload_pair(client)
            compared = client.post(
                "/api/analysis/experiments/compare",
                headers=headers,
                json={"paper_ids": ids},
            )
            assert compared.status_code == 200
            profile_ids = [profile["profile_id"] for profile in compared.json()["profiles"]]
            assert all(profile_ids)
            assert all(
                evidence["locator"]["source_span_id"]
                for row in compared.json()["matrix"]
                for cell in row["cells"]
                for evidence in cell["evidence"]
            )
            generated_call_count = len(calls)
            # A later comparison in another tab must not replace the immutable
            # profiles that this screen is about to save.
            for paper_id in ids:
                paper = store.get(paper_id)
                source_version_id = store.paper_source_version_id(paper.workspace_id, paper.id)
                alternate = analyze_full_text_paper(
                    paper, store.paper_pages(paper.workspace_id, paper.id), [],
                    source_version_id=source_version_id,
                    model="alternate-model", prompt_version="alternate-v1",
                )
                alternate_snapshot = profile_snapshot(alternate)
                alternate_snapshot.update({
                    "generation_provider": "local",
                    "generation_model": "alternate-model",
                    "generation_prompt_version": "alternate-v1",
                    "extraction_mode": "deterministic_local",
                })
                store.save_experiment_profile_snapshot(
                    paper.workspace_id, alternate, provider="local",
                    snapshot_override=alternate_snapshot,
                )

            saved = client.post(
                "/api/comparisons",
                headers=headers,
                json={
                    "name": "Evidence snapshot",
                    "paper_ids": ids,
                    "experiment_analysis": True,
                    "experiment_profile_ids": profile_ids,
                },
            )

        assert saved.status_code == 201
        assert len(calls) == generated_call_count
        body = saved.json()
        assert len(body["result"]) == 2
        assert len(body["citation_snapshot"]) == 2
        for snapshot in body["citation_snapshot"]:
            assert snapshot["experiment_profile_id"] in profile_ids
            assert snapshot["source_version_id"]
            assert snapshot["source_span_ids"]
            assert snapshot["generation_provider"] == "openai"
            assert snapshot["generation_model"] == "gpt-5.6-luna"
            assert snapshot["generation_prompt_version"] == "experiment-analysis-v2"
            assert snapshot["extraction_mode"] == "llm_verified"
            assert isinstance(snapshot["figure_table_refs"], list)
    finally:
        main.app.dependency_overrides.clear()


def test_idempotent_profile_retry_binds_each_quote_to_its_original_span(tmp_path, monkeypatch):
    store = _setup(tmp_path)
    monkeypatch.setattr(
        main,
        "generate_experiment_proposals",
        lambda selection, pages: ProposalGeneration(
            selection.provider,
            selection.model,
            "experiment-analysis-v2",
            (
                {"category": "conditions", "page": 1, "quote": "The trial used five random seeds.", "comparator": None},
                {"category": "observations", "page": 1, "quote": "Accuracy was 0.72.", "comparator": None},
            ),
            {},
            True,
        ),
    )
    create_source_import = store.create_source_import

    def create_source_import_reordered(*args, **kwargs):
        source, spans = create_source_import(*args, **kwargs)
        return source, list(reversed(spans))

    save_profile = store.save_experiment_profile_snapshot
    save_attempts = 0

    def fail_after_first_source_import(*args, **kwargs):
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts == 1:
            raise RuntimeError("simulated failure after source import")
        return save_profile(*args, **kwargs)

    monkeypatch.setattr(store, "create_source_import", create_source_import_reordered)
    monkeypatch.setattr(store, "save_experiment_profile_snapshot", fail_after_first_source_import)
    try:
        with TestClient(main.app, raise_server_exceptions=False) as client:
            headers, ids = _upload_pair(client)
            first = client.post(
                "/api/analysis/experiments/compare", headers=headers, json={"paper_ids": ids},
            )
            retried = client.post(
                "/api/analysis/experiments/compare", headers=headers, json={"paper_ids": ids},
            )
        assert first.status_code == 500
        assert retried.status_code == 200
        workspace_id = store.get(ids[0]).workspace_id
        for profile in retried.json()["profiles"]:
            for field in (
                "purpose", "design", "observations",
                "author_interpretations", "limitations",
            ):
                for evidence in profile[field]:
                    locator = evidence["locator"]
                    span = store.get_source_span(workspace_id, locator["source_span_id"])
                    assert span.text == locator["quote"]
                    assert span.page == locator["page"]
                    assert span.bbox == locator["bbox"]
                    assert span.cell == locator["cell"]
                    assert span.locator["profile_field"] == field
    finally:
        main.app.dependency_overrides.clear()


def test_deleting_paper_removes_derived_experiment_source_and_spans(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(
        main,
        "generate_experiment_proposals",
        lambda selection, pages: ProposalGeneration(
            selection.provider, selection.model, "experiment-analysis-v2", (), {}, False, "disabled",
        ),
    )
    try:
        with TestClient(main.app) as client:
            headers, ids = _upload_pair(client)
            compared = client.post(
                "/api/analysis/experiments/compare", headers=headers, json={"paper_ids": ids},
            )
            derived_id = compared.json()["profiles"][0]["derived_source_version_id"]
            assert client.get(
                f"/api/graph/sources/{derived_id}/spans", headers=headers,
            ).status_code == 200
            assert client.delete(f"/api/papers/{ids[0]}", headers=headers).status_code == 204
            assert client.get(
                f"/api/graph/sources/{derived_id}/spans", headers=headers,
            ).status_code == 404
    finally:
        main.app.dependency_overrides.clear()
