from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app import main
from app.database import Base
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'summary.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: LocalOriginalStorage(tmp_path / "originals")


def _upload(client: TestClient) -> str:
    response = client.post(
        "/api/papers/upload", headers={"X-Dev-User": "alice"},
        files={"files": ("paper.md", b"# Method\nThe method improves accuracy by 12%.\n# Limits\nFuture work needs more data.", "text/markdown")},
    )
    assert response.status_code == 200
    return response.json()[0]["paper"]["id"]


def test_paper_summary_falls_back_to_page_linked_extractive_markdown(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        with TestClient(main.app) as client:
            paper_id = _upload(client)
            response = client.post(f"/api/papers/{paper_id}/summary", headers={"X-Dev-User": "alice"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["generation_mode"] == "local_fallback"
        assert payload["fallback_reason"] == "api_key_missing"
        assert payload["summary"].startswith("## ")
        assert payload["citations"][0]["paper_id"] == paper_id
        assert payload["citations"][0]["page"] == 1
        assert "improves accuracy" in payload["citations"][0]["excerpt"]
    finally:
        main.app.dependency_overrides.clear()


def test_paper_summary_uses_bounded_llm_and_preserves_citations(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured = {}

    def fake_generate(paper, citations, timeout_seconds):
        captured["timeout"] = timeout_seconds
        captured["citation_count"] = len(citations)
        return "## 要点\n\n精度が改善した。 [1]"

    monkeypatch.setattr(main, "_generate_paper_summary_with_llm", fake_generate)
    try:
        with TestClient(main.app) as client:
            paper_id = _upload(client)
            response = client.post(f"/api/papers/{paper_id}/summary", headers={"X-Dev-User": "alice"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["generation_mode"] == "llm"
        assert payload["model"] == "gpt-5.4-nano"
        assert payload["citations"] and payload["citations"][0]["page"] == 1
        assert 0 < captured["timeout"] <= 20
        assert 0 < captured["citation_count"] <= 6
    finally:
        main.app.dependency_overrides.clear()
