import json
import time
import threading

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import main
from app.database import Base, DiscoveryItemRecord, DiscoverySearchCandidateRecord, DiscoverySearchSessionRecord, PaperExternalIdentifierRecord, PaperRecord, SourceSpanRecord, SourceVersionRecord
from app.models import Chunk, DiscoverySearchRequest
from app.discovery_providers import (
    DiscoveryProviderError,
    canonical_doi,
    fetch_doi_metadata,
    fetch_provider_paper as fetch_discovery_provider_paper,
)
from app import discovery_providers
from app.discovery_workflow import federate_provider_rows
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
        (httpx.Response(404), "not_found"),
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.1103/PhysRevE.51.980", "10.1103/physreve.51.980"),
        ("doi:10.1103/PhysRevE.51.980", "10.1103/physreve.51.980"),
        ("https://doi.org/10.1103/PhysRevE.51.980", "10.1103/physreve.51.980"),
        ("https://www.doi.org/10.1103/PhysRevE.51.980", "10.1103/physreve.51.980"),
        ("not a doi", None),
    ],
)
def test_canonical_doi_accepts_supported_forms(value, expected):
    assert canonical_doi(value) == expected


def test_federated_discovery_merges_www_doi_resolver_form_with_bare_doi():
    body = DiscoverySearchRequest(query="test", providers=["semantic_scholar", "openalex"])

    def fetch(provider):
        doi = "https://www.doi.org/10.1103/PhysRevE.51.980" if provider == "semantic_scholar" else "10.1103/physreve.51.980"
        return provider, [{
            "provider_paper_id": f"{provider}-id", "title": "Same paper", "authors": [], "year": 1995,
            "abstract": "", "citation_count": 0, "external_ids": {"doi": doi}, "rrf": 0.01,
        }], None, None, None

    ordered, _, _, _ = federate_provider_rows(body, fetch)
    assert len(ordered) == 1
    assert ordered[0]["canonical_key"] == "doi:10.1103/physreve.51.980"


def test_explicit_doi_uses_crossref_metadata_with_mock_transport():
    def handler(request):
        assert request.url.host == "api.crossref.org"
        return httpx.Response(200, json={"message": {
            "DOI": "10.1103/PhysRevE.51.980", "title": ["Crossref title"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "published-print": {"date-parts": [[1995, 2, 1]]}, "abstract": "<jats:p>Abstract &amp; evidence.</jats:p>",
        }})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        metadata = fetch_doi_metadata("doi:10.1103/PhysRevE.51.980", client=client)
    finally:
        client.close()
    assert metadata["provider"] == "crossref"
    assert metadata["external_ids"] == {"doi": "10.1103/physreve.51.980"}
    assert metadata["title"] == "Crossref title" and metadata["year"] == 1995
    assert metadata["abstract"] == "Abstract & evidence."
    assert metadata["provider_snapshot"]["abstract"] == "<jats:p>Abstract &amp; evidence.</jats:p>"


def test_explicit_doi_falls_back_to_semantic_scholar_once_after_crossref_failure(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    calls = []
    monkeypatch.setattr(discovery_providers, "fetch_semantic_paper", lambda identifier: calls.append(identifier) or _paper("semantic-doi", "10.1103/PhysRevE.51.980"))
    try:
        metadata = fetch_doi_metadata("10.1103/PhysRevE.51.980", client=client)
    finally:
        client.close()
    assert metadata["provider"] == "semantic_scholar"
    assert calls == ["DOI:10.1103/physreve.51.980"]


def test_explicit_doi_rejects_crossref_snapshot_for_a_different_doi(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"message": {
        "DOI": "10.1103/PhysRevE.51.981", "title": ["Wrong paper"],
    }})))
    monkeypatch.setattr(
        discovery_providers, "fetch_semantic_paper",
        lambda identifier: (_ for _ in ()).throw(SemanticScholarError("invalid_response")),
    )
    try:
        with pytest.raises(DiscoveryProviderError) as excinfo:
            fetch_doi_metadata("10.1103/PhysRevE.51.980", client=client)
    finally:
        client.close()
    assert excinfo.value.code == "invalid_response"


def test_explicit_doi_rejects_semantic_snapshot_for_a_different_doi(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    snapshot = _paper("semantic-wrong", "10.1103/PhysRevE.51.981")
    monkeypatch.setattr(discovery_providers, "fetch_semantic_paper", lambda identifier: snapshot)
    try:
        with pytest.raises(DiscoveryProviderError) as excinfo:
            fetch_doi_metadata("10.1103/PhysRevE.51.980", client=client)
    finally:
        client.close()
    assert excinfo.value.code == "invalid_response"


def test_explicit_doi_allows_semantic_snapshot_without_a_doi(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    snapshot = _paper("semantic-no-doi", "10.1103/PhysRevE.51.980")
    snapshot["externalIds"] = {"ArXiv": "2501.00001"}
    monkeypatch.setattr(discovery_providers, "fetch_semantic_paper", lambda identifier: snapshot)
    try:
        metadata = fetch_doi_metadata("10.1103/PhysRevE.51.980", client=client)
    finally:
        client.close()
    assert metadata["provider"] == "semantic_scholar"
    assert metadata["external_ids"]["doi"] == "10.1103/physreve.51.980"


def test_explicit_doi_not_found_after_crossref_and_semantic_fallback(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    monkeypatch.setattr(
        discovery_providers, "fetch_semantic_paper",
        lambda identifier: (_ for _ in ()).throw(SemanticScholarError("not_found")),
    )
    try:
        with pytest.raises(DiscoveryProviderError) as excinfo:
            fetch_doi_metadata("10.1103/PhysRevE.51.980", client=client)
    finally:
        client.close()
    assert excinfo.value.code == "not_found"


def test_crossref_not_found_remains_not_found_when_semantic_fallback_is_unavailable(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    monkeypatch.setattr(
        discovery_providers, "fetch_semantic_paper",
        lambda identifier: (_ for _ in ()).throw(SemanticScholarError("unavailable")),
    )
    try:
        with pytest.raises(DiscoveryProviderError) as excinfo:
            fetch_doi_metadata("10.1103/PhysRevE.51.980", client=client)
    finally:
        client.close()
    assert excinfo.value.code == "not_found"


@pytest.mark.parametrize(
    ("provider", "provider_id", "external_ids", "response"),
    [
        (
            "openalex", "W123", {"doi": "10.1000/example"},
            httpx.Response(200, json={
                "id":"https://openalex.org/W123", "title":"OpenAlex paper",
                "doi":"https://doi.org/10.1000/example", "authorships":[],
                "publication_year":2025, "cited_by_count":1,
            }),
        ),
        (
            "cinii", "https://cir.nii.ac.jp/crid/123", {"doi":"10.1000/example"},
            httpx.Response(200, json={"@graph":[{
                "@id":"https://cir.nii.ac.jp/crid/123", "title":"CiNii paper",
                "doi":"10.1000/example", "creator":["Ada"], "publicationDate":"2025",
            }]}),
        ),
        (
            "jstage", "JSTAGE-123", {"doi":"10.1000/example"},
            httpx.Response(200, text=(
                '<feed xmlns="http://www.w3.org/2005/Atom" '
                'xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">'
                "<entry><id>JSTAGE-123</id><title>J-STAGE paper</title>"
                "<prism:doi>10.1000/example</prism:doi></entry></feed>"
            )),
        ),
    ],
)
def test_provider_candidate_refetch_requires_stable_identity(
    provider, provider_id, external_ids, response, monkeypatch,
):
    monkeypatch.setenv("CINII_APP_ID", "test-app")
    client=httpx.Client(transport=httpx.MockTransport(lambda request: response))
    try:
        row=fetch_discovery_provider_paper(provider, provider_id, external_ids, client=client)
    finally:
        client.close()
    assert row["provider_paper_id"] == provider_id


def test_provider_candidate_refetch_rejects_identity_mismatch():
    client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "id":"https://openalex.org/W999", "title":"Wrong paper", "authorships":[],
        "publication_year":2025, "cited_by_count":0,
    })))
    try:
        with pytest.raises(DiscoveryProviderError) as excinfo:
            fetch_discovery_provider_paper("openalex", "W123", {"doi":"10.1000/expected"}, client=client)
    finally:
        client.close()
    assert excinfo.value.code == "invalid_response"


def test_discovery_search_returns_an_empty_provider_page_without_cursor(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(main, "search_semantic_scholar_papers", lambda *args, **kwargs: {"total": 0, "data": []})
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/discovery/search", headers={"X-Dev-User": "alice"}, json={"query": "日本語の研究", "providers": ["semantic_scholar"]})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["items"] == [] and response.json()["next_cursor"] is None


def test_discovery_search_flushes_session_before_candidate_rows(tmp_path, monkeypatch):
    _setup(tmp_path)
    observed_new_sets = []
    real_flush = Session.flush
    provider_row = {
        "provider_paper_id": "W123", "title": "OpenAlex paper",
        "authors": [], "year": 2025, "abstract": "OpenAlex abstract.", "citation_count": 1,
        "external_ids": {"openalex": "W123"}, "source_url": "https://openalex.org/W123",
    }

    def track_flush(session, *args, **kwargs):
        pending_types = {type(record) for record in session.new}
        if DiscoverySearchSessionRecord in pending_types:
            observed_new_sets.append(pending_types)
        return real_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", track_flush)
    monkeypatch.setattr(
        main, "search_discovery_provider",
        lambda *args, **kwargs: [provider_row],
    )
    monkeypatch.setattr(main, "fetch_discovery_provider_paper", lambda *args, **kwargs: provider_row)
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [Chunk(paper_id=paper_id, page=1, text=pages[0][1])])
    try:
        with TestClient(main.app) as api:
            response = api.post(
                "/api/discovery/search", headers={"X-Dev-User": "alice"},
                json={"query": "parent ordering", "providers": ["openalex"]},
            )
            imported = api.post(
                "/api/discovery/imports", headers={"X-Dev-User": "alice"},
                json={
                    "search_session_id": response.json()["search_session_id"],
                    "candidate_ids": [response.json()["items"][0]["candidate_id"]],
                },
            )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert imported.status_code == 200
    assert imported.json()["items"][0]["status"] == "imported"
    assert any(
        DiscoverySearchSessionRecord in pending
        and DiscoverySearchCandidateRecord not in pending
        for pending in observed_new_sets
    )


def test_default_federated_discovery_uses_all_default_providers_with_mocked_gateways(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(main, "search_semantic_scholar_papers", lambda *args, **kwargs: {"data": [_paper("s-default", "10.1/default")]})
    seen = []
    def provider(provider, *args, **kwargs):
        seen.append(provider)
        return [{"provider_paper_id": f"{provider}-1", "title": f"{provider} paper", "authors": [], "year": 2024, "abstract": "", "citation_count": 0, "external_ids": {provider: f"{provider}-1"}, "source_url": ""}]
    monkeypatch.setattr(main, "search_discovery_provider", provider)
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/discovery/search", headers={"X-Dev-User": "alice"}, json={"query": "federated test"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert set(seen) == {"openalex", "jstage"}
    assert response.json()["providers_used"] == ["semantic_scholar", "openalex", "jstage"]
    assert len(response.json()["items"]) == 3


def test_federated_provider_calls_are_parallel_but_provider_query_variants_stay_sequential(tmp_path, monkeypatch):
    _setup(tmp_path)
    lock = threading.Lock(); arrived = []; release = threading.Event(); observed = []
    def provider(name, *args, **kwargs):
        with lock:
            arrived.append(name)
            if len(arrived) == 2: release.set()
        observed.append(release.wait(0.5))
        return [{"provider_paper_id": f"{name}-1", "title": name, "authors": [], "year": 2024, "abstract": "", "citation_count": 0, "external_ids": {name: f"{name}-1"}, "source_url": ""}]
    monkeypatch.setattr(main, "search_discovery_provider", provider)
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/discovery/search", headers={"X-Dev-User": "alice"}, json={"query": "parallel", "providers": ["openalex", "jstage"]})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert set(arrived) == {"openalex", "jstage"}
    assert observed == [True, True]


def test_discovery_search_cursor_and_workspace_write_boundary(tmp_path, monkeypatch):
    _setup(tmp_path)
    calls = []
    page = [_paper(f"s{index}", f"10.1000/{index}") for index in range(40)]

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
            response = api.post("/api/discovery/search", headers=headers, json={"query": "日本語の研究", "sort": "relevance", "providers": ["semantic_scholar"]})
            follow_up = api.post("/api/discovery/search", headers=headers, json={"query": "日本語の研究", "sort": "relevance", "providers": ["semantic_scholar"], "cursor": response.json()["next_cursor"]})
            duplicate_candidate_id = response.json()["items"][0]["candidate_id"]
            duplicate_candidate_import = api.post(
                "/api/discovery/imports",
                headers=headers,
                json={
                    "search_session_id": response.json()["search_session_id"],
                    "candidate_ids": [duplicate_candidate_id, duplicate_candidate_id],
                },
            )
            workspace = api.post("/api/workspaces", headers=headers, json={"name": "Shared"}).json()
            api.get("/api/me", headers={"X-Dev-User": "viewer"})
            api.post(f"/api/workspaces/{workspace['id']}/members", headers=headers, json={"subject": "viewer", "role": "viewer"})
            forbidden = api.post("/api/discovery/search", headers={"X-Dev-User": "viewer", "X-Workspace-ID": workspace["id"]}, json={"query": "test"})
            forbidden_import = api.post("/api/discovery/imports", headers={"X-Dev-User": "viewer", "X-Workspace-ID": workspace["id"]}, json={"provider_paper_ids": ["s1"], "search_context": {"query": "test"}})
            invalid_years = api.post("/api/discovery/search", headers=headers, json={"query": "test", "year_from": 2025, "year_to": 2024})
            replayed_cursor = api.post("/api/discovery/search", headers=headers, json={"query": "different", "cursor": response.json()["next_cursor"]})
            cursor = response.json()["next_cursor"]
            tampered_cursor = api.post(
                "/api/discovery/search", headers=headers,
                json={"query": "日本語の研究", "cursor": f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}"},
            )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["items"][0]["content_scope"] == "abstract_only"
    assert response.json()["expires_at"]
    assert response.json()["next_cursor"] and response.json()["next_cursor"] != "20"
    assert follow_up.status_code == 200 and calls == [{"offset": 0, "limit": 50, "sort": "relevance", "cursor_token": None, "year_from": None, "year_to": None}]
    assert [item["provider_paper_id"] for item in follow_up.json()["items"]] == [f"s{index}" for index in range(20, 40)]
    assert forbidden.status_code == forbidden_import.status_code == 403
    assert fetch_calls == []
    assert replayed_cursor.status_code == 200
    assert invalid_years.status_code == tampered_cursor.status_code == duplicate_candidate_import.status_code == 422


def test_discovery_bulk_results_use_stable_session_cursor(tmp_path, monkeypatch):
    _setup(tmp_path)
    calls = []

    def fake_search(*args, **kwargs):
        calls.append((args, kwargs))
        return {"data": [_paper(f"s{index}", f"10.1000/{index}") for index in range(30)], "token": "provider-next"}

    monkeypatch.setattr(main, "search_semantic_scholar_papers", fake_search)
    try:
        with TestClient(main.app) as api:
            headers = {"X-Dev-User": "alice"}
            first = api.post("/api/discovery/search", headers=headers, json={"query": "日本語の研究", "sort": "newest", "providers": ["semantic_scholar"]})
            second = api.post("/api/discovery/search", headers=headers, json={"query": "日本語の研究", "sort": "newest", "providers": ["semantic_scholar"], "cursor": first.json()["next_cursor"]})
    finally:
        main.app.dependency_overrides.clear()
    assert first.status_code == second.status_code == 200
    assert calls[0][0] == ("日本語の研究",)
    assert len(calls) == 1
    assert [item["provider_paper_id"] for item in second.json()["items"]] == [f"s{index}" for index in range(20, 30)]
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


def test_discovery_import_flushes_parent_before_external_identifier_rows(tmp_path, monkeypatch):
    _setup(tmp_path)
    observed_new_sets = []
    real_flush = Session.flush

    def track_flush(session, *args, **kwargs):
        pending_types = {type(record) for record in session.new}
        if PaperRecord in pending_types:
            observed_new_sets.append(pending_types)
        return real_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", track_flush)
    monkeypatch.setattr(main, "fetch_semantic_scholar_paper", lambda paper_id: _paper("s-flush", "10.1103/PhysRevE.51.980"))
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [Chunk(paper_id=paper_id, page=1, text=pages[0][1])])
    try:
        with TestClient(main.app) as api:
            response = api.post(
                "/api/discovery/imports", headers={"X-Dev-User": "alice"},
                json={"provider_paper_ids": ["s-flush"], "search_context": {"query": "test"}},
            )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200 and response.json()["items"][0]["status"] == "imported"
    assert any(
        PaperRecord in pending and PaperExternalIdentifierRecord not in pending
        for pending in observed_new_sets
    )


def test_discovery_www_doi_and_direct_bare_doi_resolve_to_one_paper(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    semantic_snapshot = _paper("semantic-www", "https://www.doi.org/10.1103/PhysRevE.51.980")
    direct_metadata = {
        "provider": "crossref", "provider_paper_id": "10.1103/physreve.51.980",
        "external_ids": {"doi": "10.1103/physreve.51.980"}, "title": "Crossref paper",
        "authors": [], "year": 1995, "abstract": "", "license": "crossref_api_metadata",
        "rate_limit_policy": "crossref_public_pool", "provider_snapshot": {"DOI": "10.1103/PhysRevE.51.980"},
    }
    monkeypatch.setattr(main, "fetch_semantic_scholar_paper", lambda paper_id: semantic_snapshot)
    monkeypatch.setattr(main, "fetch_doi_metadata", lambda doi: direct_metadata)
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [Chunk(paper_id=paper_id, page=1, text=pages[0][1])])
    try:
        with TestClient(main.app) as api:
            headers = {"X-Dev-User": "alice"}
            imported = api.post("/api/discovery/imports", headers=headers, json={"provider_paper_ids": ["semantic-www"], "search_context": {"query": "test"}})
            direct = api.post("/api/papers/external", headers=headers, json={"identifier": "10.1103/PhysRevE.51.980"})
    finally:
        main.app.dependency_overrides.clear()
    assert imported.status_code == direct.status_code == 200
    assert direct.json()["id"] == imported.json()["items"][0]["paper_id"]
    with sessionmaker(bind=engine)() as session:
        papers = session.scalars(select(PaperRecord)).all()
        doi = session.scalar(select(PaperExternalIdentifierRecord).where(PaperExternalIdentifierRecord.provider == "doi"))
    assert len(papers) == 1 and doi.identifier == "10.1103/physreve.51.980"


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
            search = api.post("/api/discovery/search", headers=headers, json={"query": "test", "providers": ["semantic_scholar"]})
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


def test_external_doi_endpoint_persists_crossref_provenance_and_deduplicates_canonical_input(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    crossref_metadata = {
        "provider": "crossref", "provider_paper_id": "10.1103/physreve.51.980",
        "external_ids": {"doi": "10.1103/physreve.51.980"}, "title": "Crossref paper",
        "authors": ["Ada"], "year": 1995, "abstract": "Crossref abstract & evidence.",
        "license": "crossref_api_metadata", "rate_limit_policy": "crossref_public_pool",
        "provider_snapshot": {"DOI": "10.1103/PhysRevE.51.980", "title": ["Crossref paper"], "abstract": "<jats:p>Crossref abstract &amp; evidence.</jats:p>"},
    }
    fetch_calls = []
    monkeypatch.setattr(
        main, "fetch_doi_metadata",
        lambda doi: fetch_calls.append(doi) or crossref_metadata,
    )
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
                    "identifier": "https://doi.org/10.1103/PhysRevE.51.980",
                    "title": "Forged title", "abstract": "Forged abstract",
                    "authors": ["Forged author"], "year": 1900,
                },
            )
            duplicate = api.post("/api/papers/external", headers=headers, json={"identifier": "doi:10.1103/physreve.51.980"})
    finally:
        main.app.dependency_overrides.clear()
    assert created.status_code == 200
    assert duplicate.status_code == 200 and duplicate.json()["id"] == created.json()["id"]
    assert fetch_calls == ["10.1103/physreve.51.980"]
    assert created.json()["content_scope"] == "abstract_only"
    with sessionmaker(bind=engine)() as session:
        papers = session.scalars(select(PaperRecord)).all()
        version = session.scalar(select(SourceVersionRecord).where(SourceVersionRecord.kind == "external_metadata"))
        span = session.scalar(select(SourceSpanRecord).where(SourceSpanRecord.source_version_id == version.id))
    assert len(papers) == 1
    assert papers[0].title == "Crossref paper"
    assert papers[0].abstract == "Crossref abstract & evidence."
    assert version.paper_id == created.json()["id"]
    assert version.metadata_json["snapshot"]["provider"] == "crossref"
    assert version.metadata_json["license"] == "crossref_api_metadata"
    assert version.metadata_json["rate_limit_policy"] == "crossref_public_pool"
    assert version.metadata_json["fetched_at"]
    assert span.text == "Crossref abstract & evidence."
    assert "<jats:p>" not in span.text


def test_external_doi_semantic_fallback_persists_abstract_provenance(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    fallback_snapshot = {**_paper("semantic-doi", "10.1103/PhysRevE.51.980"), "title": "Fallback paper", "abstract": "Semantic fallback abstract."}
    monkeypatch.setattr(
        discovery_providers, "_crossref_doi_metadata",
        lambda *args, **kwargs: (_ for _ in ()).throw(DiscoveryProviderError("unavailable")),
    )
    monkeypatch.setattr(discovery_providers, "fetch_semantic_paper", lambda identifier: fallback_snapshot)
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [
        Chunk(paper_id=paper_id, page=1, text=pages[0][1])
    ])
    try:
        with TestClient(main.app) as api:
            response = api.post(
                "/api/papers/external", headers={"X-Dev-User": "alice"},
                json={"identifier": "doi:10.1103/PhysRevE.51.980"},
            )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    with sessionmaker(bind=engine)() as session:
        version = session.scalar(select(SourceVersionRecord).where(SourceVersionRecord.kind == "external_metadata"))
        span = session.scalar(select(SourceSpanRecord).where(SourceSpanRecord.source_version_id == version.id))
    assert version.metadata_json["provider"] == "semantic_scholar"
    assert version.metadata_json["snapshot"]["provider_snapshot"] == fallback_snapshot
    assert version.metadata_json["license"] == "semantic_scholar_api"
    assert version.metadata_json["rate_limit_policy"] == "api_key_intro_1_rps"
    assert version.metadata_json["fetched_at"]
    assert span.text == "Semantic fallback abstract."


def test_external_arxiv_endpoint_persists_abstract_only_metadata(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    atom = """<feed xmlns=\"http://www.w3.org/2005/Atom\"><entry><id>http://arxiv.org/abs/2501.00001v2</id><title>arXiv paper</title><summary>arXiv abstract.</summary><published>2025-01-02T00:00:00Z</published><author><name>Ada</name></author></entry></feed>"""
    monkeypatch.setattr(
        main.httpx, "get",
        lambda *args, **kwargs: httpx.Response(
            200, text=atom,
            request=httpx.Request("GET", "https://export.arxiv.org/api/query"),
        ),
    )
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(main, "chunk_pages", lambda pages, paper_id: [Chunk(paper_id=paper_id, page=1, text=pages[0][1])])
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/papers/external", headers={"X-Dev-User": "alice"}, json={"identifier": "arxiv:2501.00001"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200 and response.json()["content_scope"] == "abstract_only"
    with sessionmaker(bind=engine)() as session:
        identifier = session.scalar(select(PaperExternalIdentifierRecord).where(PaperExternalIdentifierRecord.provider == "arxiv"))
        span = session.scalar(select(SourceSpanRecord))
    assert identifier.identifier == "2501.00001"
    assert span.text == "arXiv abstract."


@pytest.mark.parametrize(
    ("exception", "status", "code"),
    [
        (httpx.HTTPStatusError("not found", request=httpx.Request("GET", "https://export.arxiv.org"), response=httpx.Response(404)), 404, "external_paper_not_found"),
        (httpx.ReadTimeout("timeout", request=httpx.Request("GET", "https://export.arxiv.org")), 503, "external_provider_unavailable"),
    ],
)
def test_external_arxiv_provider_failures_are_structured_and_atomic(tmp_path, monkeypatch, exception, status, code):
    engine = _setup(tmp_path)
    monkeypatch.setattr(main.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(exception))
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/papers/external", headers={"X-Dev-User": "alice"}, json={"identifier": "2501.00001"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == status and response.json()["detail"]["code"] == code
    with sessionmaker(bind=engine)() as session:
        assert session.scalars(select(PaperRecord)).all() == []


def test_external_arxiv_success_without_entry_is_not_found_and_atomic(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    monkeypatch.setattr(
        main.httpx, "get",
        lambda *args, **kwargs: httpx.Response(
            200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>',
            request=httpx.Request("GET", "https://export.arxiv.org/api/query"),
        ),
    )
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/papers/external", headers={"X-Dev-User": "alice"}, json={"identifier": "2501.00001"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "external_paper_not_found"
    with sessionmaker(bind=engine)() as session:
        assert session.scalars(select(PaperRecord)).all() == []


def test_external_arxiv_rejects_mismatched_response_identity_without_persisting(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    atom = """<feed xmlns=\"http://www.w3.org/2005/Atom\"><entry><id>https://arxiv.org/abs/2502.00001v1</id><title>Wrong paper</title><summary>Wrong abstract.</summary><published>2025-01-02T00:00:00Z</published></entry></feed>"""
    monkeypatch.setattr(
        main.httpx, "get",
        lambda *args, **kwargs: httpx.Response(
            200, text=atom,
            request=httpx.Request("GET", "https://export.arxiv.org/api/query"),
        ),
    )
    try:
        with TestClient(main.app) as api:
            response = api.post(
                "/api/papers/external", headers={"X-Dev-User": "alice"},
                json={"identifier": "2501.00001"},
            )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_provider_response"
    with sessionmaker(bind=engine)() as session:
        assert session.scalars(select(PaperRecord)).all() == []


def test_external_doi_upsert_rolls_back_flushed_parent_when_provenance_fails(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    metadata = {
        "provider": "crossref", "provider_paper_id": "10.1103/physreve.51.980",
        "external_ids": {"doi": "10.1103/physreve.51.980"}, "title": "Crossref paper",
        "authors": [], "year": 1995, "abstract": "Abstract", "license": "crossref_api_metadata",
        "rate_limit_policy": "crossref_public_pool", "provider_snapshot": {"DOI": "10.1103/PhysRevE.51.980"},
    }
    monkeypatch.setattr(main, "fetch_doi_metadata", lambda doi: metadata)
    monkeypatch.setattr(main, "embedding_config", lambda: ("local", "test-model"))
    monkeypatch.setattr(
        PaperStore, "_ensure_external_metadata_source",
        staticmethod(lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("provenance failed"))),
    )
    try:
        with TestClient(main.app, raise_server_exceptions=False) as api:
            response = api.post(
                "/api/papers/external", headers={"X-Dev-User": "alice"},
                json={"identifier": "10.1103/PhysRevE.51.980"},
            )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 500
    with sessionmaker(bind=engine)() as session:
        assert session.scalars(select(PaperRecord)).all() == []
        assert session.scalars(select(PaperExternalIdentifierRecord)).all() == []


@pytest.mark.parametrize(
    ("provider_error", "status", "code"),
    [
        ("invalid_response", 502, "invalid_provider_response"),
        ("not_found", 404, "external_paper_not_found"),
        ("rate_limited", 429, "external_provider_rate_limited"),
        ("unavailable", 503, "external_provider_unavailable"),
    ],
)
def test_external_doi_failures_are_structured_and_leave_no_paper(tmp_path, monkeypatch, provider_error, status, code):
    engine = _setup(tmp_path)
    monkeypatch.setattr(main, "fetch_doi_metadata", lambda doi: (_ for _ in ()).throw(DiscoveryProviderError(provider_error)))
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/papers/external", headers={"X-Dev-User": "alice"}, json={"identifier": "10.1103/PhysRevE.51.980"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == status and response.json()["detail"]["code"] == code
    with sessionmaker(bind=engine)() as session:
        assert session.scalars(select(PaperRecord)).all() == []


def test_invalid_external_identifier_is_rejected_before_any_provider_call(tmp_path, monkeypatch):
    engine = _setup(tmp_path)
    monkeypatch.setattr(main, "fetch_doi_metadata", lambda doi: pytest.fail("provider should not be called"))
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/papers/external", headers={"X-Dev-User": "alice"}, json={"identifier": "10.invalid"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_external_identifier"
    with sessionmaker(bind=engine)() as session:
        assert session.scalars(select(PaperRecord)).all() == []


def test_discovery_search_provider_failures_have_safe_error_codes(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(main, "search_semantic_scholar_papers", lambda *args, **kwargs: (_ for _ in ()).throw(SemanticScholarError("timeout")))
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/discovery/search", headers={"X-Dev-User": "alice"}, json={"query": "test", "providers": ["semantic_scholar"]})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "external_provider_unavailable"


def test_federated_search_returns_503_only_when_every_enabled_provider_fails(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(main, "search_semantic_scholar_papers", lambda *args, **kwargs: (_ for _ in ()).throw(SemanticScholarError("unavailable")))
    monkeypatch.setattr(main, "search_discovery_provider", lambda *args, **kwargs: (_ for _ in ()).throw(DiscoveryProviderError("timeout")))
    try:
        with TestClient(main.app) as api:
            failed = api.post("/api/discovery/search", headers={"X-Dev-User": "alice"}, json={"query": "test"})
    finally:
        main.app.dependency_overrides.clear()
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "external_provider_unavailable"


def test_federated_search_keeps_partial_success_when_another_provider_fails(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(main, "search_semantic_scholar_papers", lambda *args, **kwargs: {"data": [_paper("partial")]})
    monkeypatch.setattr(main, "search_discovery_provider", lambda *args, **kwargs: (_ for _ in ()).throw(DiscoveryProviderError("timeout")))
    try:
        with TestClient(main.app) as api:
            response = api.post("/api/discovery/search", headers={"X-Dev-User": "alice"}, json={"query": "test"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["partial"] is True
    assert response.json()["providers_used"] == ["semantic_scholar"]
