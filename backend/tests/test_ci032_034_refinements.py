import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import gemini_gateway
from app.discovery_planner import plan_queries
from app.database import Base
from app.discovery_providers import _https_url, _normalized_doi
from app.models import ExperimentAnalysisErrorResponse, Principal
from app.store import PaperStore


def test_https_metadata_url_requires_absolute_credential_free_https():
    assert _https_url("https://example.org/paper") == "https://example.org/paper"
    assert _https_url("http://example.org/paper") == ""
    assert _https_url("https://user:secret@example.org/paper") == ""
    assert _https_url("javascript:alert(1)") == ""
    assert _normalized_doi("http://doi.org/10.1000/Example") == "10.1000/example"


def test_gemini_plain_and_json_modes_are_separate(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    requests = []

    def post(*args, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", args[0]), json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr(gemini_gateway.httpx, "post", post)
    assert gemini_gateway.generate_text("gemini-3.5-flash-lite", "hello", 1) == "ok"
    assert gemini_gateway.generate_json("gemini-3.5-flash-lite", "{}", 1) == "ok"
    assert "generationConfig" not in requests[0]
    assert requests[1]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_discovery_planner_uses_json_mode_and_reports_local_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gemini_gateway,
        "generate_json",
        lambda model, prompt, timeout: calls.append((model, timeout)) or '{"queries":["bounded query"]}',
    )
    planned = plan_queries(
        "original query", provider="gemini", model="gemini-test", enabled=True,
    )
    assert calls == [("gemini-test", 8.0)]
    assert planned["queries"] == ["original query", "bounded query"]
    assert planned["_generation_succeeded"] is True

    monkeypatch.setattr(
        gemini_gateway, "generate_json",
        lambda *args: "not-json",
    )
    fallback = plan_queries(
        "original query", provider="gemini", model="gemini-test", enabled=True,
    )
    assert fallback["queries"] == ["original query"]
    assert fallback["_generation_attempted"] is True
    assert fallback["_fallback_reason"] == "planner_failed"


def test_saved_comparison_keeps_canonical_partial_failures(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    _, workspace = store.ensure_user(Principal(issuer="test", subject="alice"))
    error = ExperimentAnalysisErrorResponse(
        paper_id="excluded-paper", code="full_text_required", message="full text is required",
    )
    saved = store.save_comparison(
        workspace.id, "alice", "partial", ["successful-paper"], [], analysis_errors=[error],
    )
    assert saved.analysis_errors == [error]
    assert store.list_comparisons(workspace.id)[0].analysis_errors == [error]
