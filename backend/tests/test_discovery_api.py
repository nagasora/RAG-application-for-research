import json
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, DiscoveryItemRecord, PaperExternalIdentifierRecord, PaperRecord, SourceSpanRecord, SourceVersionRecord
from app.models import Chunk, DiscoverySearchRequest
from app.semantic_scholar import SemanticScholarError, fetch_paper, search_papers
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'discovery.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    return engine


def _paper(paper_id="s2", doi="10.1000/test"):
    return {
        "paperId": paper_id, "title": "New paper", "authors": [{"name": "Ada"}],
        "year": 2025, "publicationDate": "2025-01-01", "venue": "Test Journal",
        "abstract": "Quoted abstract evidence.", "citationCount": 7,
        "externalIds": {"DOI": doi}, "url": f"https://example.test/{paper_id}",
    }


def test_semantic_scholar_search_uses_contract_specific_endpoints_with_mock_transport():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [_paper()], "token": "next-token"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        relevance = search_papers("test", limit=20, offset=20, sort="relevance", client=client)
        newest = search_papers("test", limit=20, sort="newest", cursor_token="old-token", client=client)
        citations = search_papers("日本語の研究", limit=20, sort="citation_count", year_from=2020, year_to=2024, client=client)
    finally:
        client.close()
    assert relevance["data"][0]["paperId"] == "s2"
    assert seen[0].url.path.endswith("/paper/search")
    assert seen[0].url.params["offset"] == "20"
    assert "sort" not in seen[0].url.params
    assert seen[1].url.path.endswith("/paper/search/bulk")
    assert seen[1].url.params["sort"] == "publicationDate:desc"
    assert seen[1].url.params["token"] == "old-token"
    assert citations["data"][0]["paperId"] == "s2"
    assert seen[2].url.path.endswith("/paper/search/bulk")
    assert seen[2].url.params["sort"] == "citationCount:desc"
    assert seen[2].url.params["query"] == "日本語の研究"
    assert seen[2].url.params["year"] == "2020-2024"


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(429), "rate_limited"),
        (httpx.Response(503), "unavailable"),
        (httpx.Response(200, content=b"not-json"), "invalid_response"),
    ],
)
def test_semantic_scholar_provider_errors_are_safe_with_mock_transport(response, expected_code):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
    try:
        with pytest.raises(SemanticScholarError) as excinfo:
            fetch_paper("s2", client)
    finally:
        client.close()
    assert excinfo.value.code == expected_code


def test_semantic_scholar_timeout_is_safe_with_mock_transport():
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    try:
        with pytest.raises(SemanticScholarError) as excinfo:
            search_papers("研究", client=client)
    finally:
        client.close()
    assert excinfo.value.code == "timeout"


def test_discovery_search_returns_an_empty_provider_page_without_cursor(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(main, "search_semantic_scholar_papers", lambda *args, **kwargs: {"total": 0, "data": []})
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/discovery/search", headers={"X-Dev-User": "alice"}, json={"query": "日本語の研究"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["items"] == [] and response.json()["next_cursor"] is None


def test_discovery_search_cursor_and_workspace_write_boundary(tmp_path, monkeypatch):
    _setup(tmp_path)
    calls = []
    page = [_paper(f"s{index}", f"10.1000/{index}") for index in range(20)]

    def fake_search(*args, **kwargs):
        calls.append(kwargs)
        return {"total": 40, "data": page}

    fetch_calls = []

    def fail_if_called(paper_id):
        fetch_calls.append(paper_id)
        raise AssertionError("viewer must not call the provider")

    monkeypatch.setattr(main, "search_semantic_scholar_papers", fake_search)
    monkeypatch.setattr(main, "fetch_semantic_scholar_paper", fail_if_called)
    try:
        with TestClient(main.app) as api:
            headers = {"X-Dev-User": "alice"}
            response = api.post("/api/discovery/search", headers=headers, json={"query": "日本語の研究", "sort": "relevance"})
            follow_up = api.post("/api/discovery/search", headers=headers, json={"query": "日本語の研究", "sort": "relevance", "cursor": response.json()["next_cursor"]})
            workspace = api.post("/api/workspaces", headers=headers, json={"name": "Shared"}).json()
            api.get("/api/me", headers={"X-Dev-User": "viewer"})
            api.post(f"/api/workspaces/{workspace['id']}/members", headers=headers, json={"subject": "viewer", "role": "viewer"})
            forbidden = api.post("/api/discovery/search", headers={"X-Dev-User": "viewer", "X-Workspace-ID": workspace["id"]}, json={"query": "test"})
            forbidden_import = api.post("/api/discovery/imports", headers={"X-Dev-User": "viewer", "X-Workspace-ID": workspace["id"]}, json={"provider_paper_ids": ["s1"], "search_context": {"query": "test"}})
            invalid_years = api.post("/api/discovery/search", headers=headers, json={"query": "test", "year_from": 2025, "year_to": 2024})
            mismatched_cursor = api.post("/api/discovery/search", headers=headers, json={"query": "different", "cursor": response.json()["next_cursor"]})
            cursor = response.json()["next_cursor"]
            tampered_cursor = api.post(
                "/api/discovery/search", headers=headers,
                json={"query": "日本語の研究", "cursor": f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}"},
            )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["items"][0]["content_scope"] == "abstract_only"
    assert response.json()["next_cursor"] and response.json()["next_cursor"] != "20"
    assert follow_up.status_code == 200 and calls[0] == {"offset": 0, "limit": 20, "sort": "relevance", "cursor_token": None, "year_from": None, "year_to": None}
    assert calls[1]["offset"] == 20
    assert forbidden.status_code == forbidden_import.status_code == 403
    assert fetch_calls == []
    assert invalid_years.status_code == mismatched_cursor.status_code == tampered_cursor.status_code == 422


def test_discovery_bulk_cursor_keeps_provider_token(tmp_path, monkeypatch):
    _setup(tmp_path)
    calls = []

    def fake_search(*args, **kwargs):
        calls.append((args, kwargs))
        return {"data": [_paper()], "token": "provider-next"}

    monkeypatch.setattr(main, "search_semantic_scholar_papers", fake_search)
    try:
        with TestClient(main.app) as api:
            headers = {"X-Dev-User": "alice"}
            first = api.post("/api/discovery/search", headers=headers, json={"query": "日本語の研究", "sort": "newest"})
            second = api.post("/api/discovery/search", headers=headers, json={"query": "日本語の研究", "sort": "newest", "cursor": first.json()["next_cursor"]})
    finally:
        main.app.dependency_overrides.clear()
    assert first.status_code == second.status_code == 200
    assert calls[0][0] == ("日本語の研究",)
    assert calls[1][1]["cursor_token"] == "provider-next"
    assert "provider-next" not in first.json()["next_cursor"]
    assert "日本語の研究" not in first.json()["next_cursor"]


def test_discovery_cursor_requires_secret_for_oidc_and_rejects_expired_tokens(monkeypatch):
    body = DiscoverySearchRequest(query="test")
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.delenv("DISCOVERY_CURSOR_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="DISCOVERY_CURSOR_SECRET"):
        main._discovery_cursor(body, offset=20)

    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("DISCOVERY_CURSOR_SECRET", "test-only-secret")
    payload = json.dumps({
        "mode": "relevance", "offset": 20, "token": None,
        "criteria_hash": main._discovery_criteria_hash(body),
    }, separators=(",", ":"), sort_keys=True).encode("utf-8")
    expired = main._discovery_cursor_cipher().encrypt_at_time(
        payload, current_time=int(time.time()) - 3_601,
    ).decode("ascii")
    with pytest.raises(HTTPException) as excinfo:
        main._discovery_position(body.model_copy(update={"cursor": f"v1.{expired}"}))
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "invalid_discovery_cursor"


def test_discovery_import_is_partial_idempotent_and_anchors_abstract(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    snapshots = {"s1": _paper("s1", "https://doi.org/10.1000/one"), "s2": _paper("s2", "10.1000/two"), "s3": {"paperId": "s3"}}

    def fake_fetch(paper_id):
        if paper_id == "s2":
            raise SemanticScholarError("rate_limited")
        return snapshots[paper_id]

    monkeypatch.setattr(main, "fetch_semantic_scholar_paper", fake_fetch)
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [
        Chunk(paper_id=paper_id, page=1, text=pages[0][1])
    ])
    payload = {"provider_paper_ids": ["s1", "s2", "s3"], "search_context": {"query": "test", "sort": "citation_count"}}
    try:
        with TestClient(main.app) as api:
            headers = {"X-Dev-User": "alice"}
            imported = api.post("/api/discovery/imports", headers=headers, json=payload)
            duplicate = api.post("/api/discovery/imports", headers=headers, json={**payload, "provider_paper_ids": ["s1"]})
            bob = api.post("/api/discovery/imports", headers={"X-Dev-User": "bob"}, json={**payload, "provider_paper_ids": ["s1"]})
            preview = api.post("/api/search/preview", headers=headers, json={"query": "Quoted", "paper_ids": [imported.json()["items"][0]["paper_id"]]})
    finally:
        main.app.dependency_overrides.clear()
    first, rate_limited, invalid = imported.json()["items"]
    assert imported.status_code == 200
    assert first["status"] == "imported"
    assert rate_limited == {"provider_paper_id": "s2", "status": "failed", "paper_id": None, "discovery_item_id": None, "error": "external_provider_rate_limited"}
    assert invalid == {"provider_paper_id": "s3", "status": "failed", "paper_id": None, "discovery_item_id": None, "error": "invalid_provider_response"}
    assert duplicate.json()["items"][0]["status"] == "duplicate"
    assert bob.json()["items"][0]["status"] == "imported"
    assert preview.json()["citations"][0]["evidence_scope"] == "abstract"
    with sessionmaker(bind=engine)() as session:
        item = session.scalar(select(DiscoveryItemRecord))
        identifier = session.scalar(select(PaperExternalIdentifierRecord).where(PaperExternalIdentifierRecord.provider == "doi"))
        version = session.scalar(select(SourceVersionRecord).where(SourceVersionRecord.kind == "external_metadata"))
        span = session.scalar(select(SourceSpanRecord).where(SourceSpanRecord.source_version_id == version.id))
        persisted_papers = session.scalars(select(PaperRecord)).all()
        failed_identity = session.scalar(select(PaperExternalIdentifierRecord).where(PaperExternalIdentifierRecord.identifier == "s3"))
    assert item.review_status == "accepted" and item.paper_id == first["paper_id"]
    assert item.search_context["sort"] == "citation_count"
    assert identifier.identifier == "10.1000/one"
    assert version.paper_id == first["paper_id"] and span.text == "Quoted abstract evidence."
    assert len(persisted_papers) == 2 and failed_identity is None


def test_discovery_import_rolls_back_paper_when_provenance_persistence_fails(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    monkeypatch.setattr(main, "fetch_semantic_scholar_paper", lambda paper_id: _paper("s1"))
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [Chunk(paper_id=paper_id, page=1, text=pages[0][1])])
    monkeypatch.setattr(
        PaperStore, "_ensure_external_metadata_source",
        staticmethod(lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("source persistence failed"))),
    )
    try:
        with TestClient(main.app) as api:
            response = api.post(
                "/api/discovery/imports", headers={"X-Dev-User": "alice"},
                json={"provider_paper_ids": ["s1"], "search_context": {"query": "test"}},
            )
    finally:
        main.app.dependency_overrides.clear()
    with sessionmaker(bind=engine)() as session:
        papers = session.scalars(select(PaperRecord)).all()
        identities = session.scalars(select(PaperExternalIdentifierRecord)).all()
        items = session.scalars(select(DiscoveryItemRecord)).all()
    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "failed"
    assert papers == identities == items == []


def test_malformed_external_ids_are_safe_and_import_remains_partial(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    malformed = {**_paper("bad", "10.1000/bad"), "externalIds": "not-an-object"}
    monkeypatch.setattr(main, "search_semantic_scholar_papers", lambda *args, **kwargs: {"data": [malformed]})
    monkeypatch.setattr(
        main, "fetch_semantic_scholar_paper",
        lambda paper_id: _paper("good", "10.1000/good") if paper_id == "good" else malformed,
    )
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [
        Chunk(paper_id=paper_id, page=1, text=pages[0][1])
    ])
    try:
        with TestClient(main.app) as api:
            headers = {"X-Dev-User": "alice"}
            search = api.post("/api/discovery/search", headers=headers, json={"query": "test"})
            imported = api.post(
                "/api/discovery/imports", headers=headers,
                json={"provider_paper_ids": ["good", "bad"], "search_context": {"query": "test"}},
            )
    finally:
        main.app.dependency_overrides.clear()
    assert search.status_code == 503
    assert search.json()["detail"]["code"] == "external_provider_unavailable"
    assert [item["status"] for item in imported.json()["items"]] == ["imported", "failed"]
    assert imported.json()["items"][1]["error"] == "invalid_provider_response"
    with sessionmaker(bind=engine)() as session:
        assert len(session.scalars(select(PaperRecord)).all()) == 1


def test_legacy_external_endpoint_persists_provenance_and_never_falls_back_on_provider_failure(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    monkeypatch.setattr(main, "fetch_semantic_scholar_paper", lambda paper_id: _paper("s-doi", "10.1000/direct"))
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [
        Chunk(paper_id=paper_id, page=1, text=pages[0][1])
    ])
    try:
        with TestClient(main.app) as api:
            headers = {"X-Dev-User": "alice"}
            created = api.post(
                "/api/papers/external", headers=headers,
                json={
                    "identifier": "https://doi.org/10.1000/direct",
                    "title": "Forged title", "abstract": "Forged abstract",
                    "authors": ["Forged author"], "year": 1900,
                },
            )
            monkeypatch.setattr(
                main, "fetch_semantic_scholar_paper",
                lambda paper_id: (_ for _ in ()).throw(SemanticScholarError("unavailable")),
            )
            failed = api.post(
                "/api/papers/external", headers=headers,
                json={"identifier": "10.1000/unavailable", "title": "Must not be persisted"},
            )
    finally:
        main.app.dependency_overrides.clear()
    assert created.status_code == 200
    assert created.json()["content_scope"] == "abstract_only"
    assert failed.status_code == 502
    with sessionmaker(bind=engine)() as session:
        papers = session.scalars(select(PaperRecord)).all()
        version = session.scalar(select(SourceVersionRecord).where(SourceVersionRecord.kind == "external_metadata"))
        span = session.scalar(select(SourceSpanRecord).where(SourceSpanRecord.source_version_id == version.id))
    assert len(papers) == 1
    assert papers[0].title == "New paper"
    assert papers[0].abstract == "Quoted abstract evidence."
    assert version.paper_id == created.json()["id"]
    assert version.metadata_json["snapshot"]["provider_snapshot"]["abstract"] == "Quoted abstract evidence."
    assert version.metadata_json["license"] == "semantic_scholar_api"
    assert version.metadata_json["rate_limit_policy"] == "api_key_intro_1_rps"
    assert version.metadata_json["fetched_at"]
    assert span.text == "Quoted abstract evidence."


def test_discovery_search_provider_failures_have_safe_error_codes(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(main, "search_semantic_scholar_papers", lambda *args, **kwargs: (_ for _ in ()).throw(SemanticScholarError("timeout")))
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/discovery/search", headers={"X-Dev-User": "alice"}, json={"query": "test"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "external_provider_unavailable"
