from fastapi.testclient import TestClient
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.agentic_rag import AgenticRAGResult
from app.database import Base
from app.models import Chunk, Paper, Principal, ResearchConversation, ResearchMemoryEvent, SearchRequest
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def setup_app(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'research.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: LocalOriginalStorage(tmp_path / "originals")
    return store


def headers(user, workspace_id=None):
    value = {"X-Dev-User": user}
    if workspace_id: value["X-Workspace-ID"] = workspace_id
    return value


def test_memory_context_builder_is_bounded_and_uses_relevant_store_slice():
    conversation = ResearchConversation(
        id="conversation", title="Theory", summary="S" * 4_000,
        created_by="user", created_at="2026-07-13T00:00:00+00:00",
        updated_at="2026-07-13T00:00:00+00:00",
    )

    class MemoryStore:
        def search_research_memory(self, workspace_id, conversation_id, query, *, limit):
            assert (workspace_id, conversation_id, query, limit) == (
                "workspace", "conversation", "retrieval", 8,
            )
            return [ResearchMemoryEvent(
                id="memory", conversation_id=conversation_id,
                source_message_id="message", ordinal=1, kind="hypothesis",
                content="retrieval hypothesis", created_at="2026-07-13T00:00:00+00:00",
            )]

    context = main._build_research_memory_context(
        MemoryStore(), "workspace", conversation, "retrieval",
    )
    assert len(context) <= 5_000
    assert context.startswith("S" * 2_500)
    assert "関連する長期研究メモリ" in context
    assert "retrieval hypothesis" in context


def test_search_history_failure_keeps_committed_answer_successful(
    tmp_path, monkeypatch,
):
    store = setup_app(tmp_path)
    user, workspace = store.ensure_user(Principal(issuer="test", subject="history-failure"))
    paper = Paper(
        user_id="history-failure", workspace_id=workspace.id, created_by=user.id,
        title="Evidence", status="ready", content_hash="history-failure-paper",
    )
    paper.chunks = [Chunk(
        paper_id=paper.id, page=1, section="Results",
        text="retrieval evidence supports the answer",
    )]
    store.upsert(paper, embedding_provider="local", embedding_model="local-test")
    conversation = store.create_conversation(workspace.id, user.id, "Durable answer")
    context = main.WorkspaceContext(user=user, workspace=workspace)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fail_history(*args, **kwargs):
        raise RuntimeError("sensitive database detail")

    warnings: list[str] = []
    monkeypatch.setattr(store, "add_search_history", fail_history)
    monkeypatch.setattr(main.logger, "warning", lambda message, *args: warnings.append(message))
    response = main._answer(
        SearchRequest(
            query="retrieval evidence", paper_ids=[paper.id],
            conversation_id=conversation.id,
        ),
        store,
        context,
    )

    assert response.answer
    assert response.fallback_reason == "api_key_missing"
    detail = store.get_conversation(workspace.id, conversation.id)
    assert detail.message_count == 2
    assert warnings == ["code=search_history_write_failed"]
    assert "sensitive database detail" not in "".join(warnings)


def test_assets_are_workspace_scoped_and_viewer_is_read_only(tmp_path):
    store = setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            uploaded = client.post("/api/papers/upload", headers=headers("alice"), files={"files": ("study.txt", b"retrieval evidence", "text/plain")}).json()[0]
            paper_id = uploaded["paper"]["id"]
            tag = client.post("/api/tags", headers=headers("alice"), json={"name": "Important", "color": "#ff0000"})
            tag_id = tag.json()["id"]
            attached = client.put(f"/api/papers/{paper_id}/tags", headers=headers("alice"), json={"tag_ids": [tag_id]})
            note = client.post("/api/notes", headers=headers("alice"), json={"paper_id": paper_id, "title": "Finding", "content": "Reproduce this"})

            alice, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
            bob, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
            store.add_workspace_member(workspace.id, bob.id, "viewer")
            viewer_tags = client.get("/api/tags", headers=headers("bob", workspace.id))
            viewer_notes = client.get("/api/notes", headers=headers("bob", workspace.id))
            forbidden = client.post("/api/tags", headers=headers("bob", workspace.id), json={"name": "Nope", "color": "red"})
            idor = client.patch(f"/api/notes/{note.json()['id']}", headers=headers("bob"), json={"title": "stolen"})

        assert tag.status_code == 201 and attached.status_code == 200 and note.status_code == 201
        assert viewer_tags.status_code == 200 and viewer_tags.json()[0]["id"] == tag_id
        assert viewer_notes.status_code == 200 and viewer_notes.json()[0]["title"] == "Finding"
        assert forbidden.status_code == 403
        assert idor.status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_search_history_saved_comparison_and_exports(tmp_path):
    setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            first = client.post("/api/papers/upload", headers=headers("alice"), files={"files": ("=DANGEROUS.txt", b"document retrieval evidence", "text/plain")}).json()[0]["paper"]
            second = client.post("/api/papers/upload", headers=headers("alice"), files={"files": ("Study.txt", b"document retrieval method", "text/plain")}).json()[0]["paper"]
            search = client.post("/api/search", headers=headers("alice"), json={"query": "document retrieval", "paper_ids": [first["id"]]})
            history = client.get("/api/search/history", headers=headers("alice"))
            saved = client.post("/api/comparisons", headers=headers("alice"), json={"name": "Methods", "paper_ids": [first["id"], second["id"]]})
            listed = client.get("/api/comparisons", headers=headers("alice"))
            csv_export = client.get("/api/exports/papers?format=csv", headers=headers("alice"))
            bib = client.get("/api/exports/papers?format=bibtex", headers=headers("alice"))
            ris = client.get("/api/exports/papers?format=ris", headers=headers("alice"))
            deleted = client.delete(f"/api/comparisons/{saved.json()['id']}", headers=headers("alice"))

        assert search.status_code == 200
        assert history.status_code == 200 and history.json()[0]["query"] == "document retrieval"
        assert "answer" not in history.json()[0]["result_summary"]
        assert saved.status_code == 201 and listed.json()[0]["name"] == "Methods" and deleted.status_code == 204
        assert csv_export.status_code == 200 and "'=DANGEROUS" in csv_export.text
        assert "filename=\"paperpilot-export.csv\"" in csv_export.headers["content-disposition"]
        assert "@article{" in bib.text and "TY  - JOUR" in ris.text
    finally:
        main.app.dependency_overrides.clear()


def test_research_conversation_remembers_grounded_turns(tmp_path, monkeypatch):
    store = setup_app(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    try:
        with TestClient(main.app) as client:
            paper = client.post(
                "/api/papers/upload", headers=headers("alice"),
                files={"files": ("memory.txt", b"Causal retrieval improves evidence tracing and reproducibility.", "text/plain")},
            ).json()[0]["paper"]
            created = client.post(
                "/api/research/conversations", headers=headers("alice"), json={"title": "Causal retrieval"},
            )
            conversation_id = created.json()["id"]
            response = client.post(
                "/api/search", headers=headers("alice"),
                json={"query": "retrieval evidence", "paper_ids": [paper["id"]], "conversation_id": conversation_id},
            )
            detail = client.get(f"/api/research/conversations/{conversation_id}", headers=headers("alice"))
            listed = client.get("/api/research/conversations", headers=headers("alice"))

        assert created.status_code == 201 and response.status_code == 200
        assert response.json()["conversation_id"] == conversation_id
        assert response.json()["generation_mode"] == "local_fallback"
        assert response.json()["fallback_reason"] == "api_key_missing"
        assert len(detail.json()["messages"]) == 2
        assert detail.json()["messages"][0]["role"] == "user"
        assert detail.json()["messages"][1]["citations"][0]["page"] == 1
        assert "retrieval evidence" in detail.json()["summary"]
        assert listed.json()[0]["id"] == conversation_id
        _, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
        assert store.embedding_statuses(workspace.id, [paper["id"]], "local-hash-v1")[paper["id"]] == "queued"
    finally:
        main.app.dependency_overrides.clear()


def test_first_question_title_is_normalized_at_the_conversation_boundary(tmp_path):
    setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            created = client.post(
                "/api/research/conversations", headers=headers("alice"),
                json={"title": "  最初の質問を使って、会話タイトルが自動的に作られることを確認したい  "},
            )

        assert created.status_code == 201
        assert created.json()["title"] == "最初の質問を使って、会話タイトルが自動的に作られることを確認したい"
    finally:
        main.app.dependency_overrides.clear()


def test_initial_conversation_title_is_normalized_and_bounded():
    assert main._initial_conversation_title("  a\n b\t c ") == "a b c"
    assert main._initial_conversation_title("x" * 65) == "x" * 63 + "…"


def test_search_uses_agentic_rag_and_returns_generation_metadata(tmp_path, monkeypatch):
    setup_app(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(main, "embed_texts", lambda texts, **kwargs: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(main, "_agentic_dependencies_available", lambda: True)
    monkeypatch.setattr(main, "_build_agentic_chat_model", lambda: object())

    class FakeAgent:
        def __init__(self, model, retrieve, **kwargs):
            self.retrieve = retrieve

        def run(self, query, *, memory="", deadline=None):
            citations = self.retrieve("rewritten retrieval query", 5)
            return AgenticRAGResult("根拠付き回答です [1]", citations[:1], ["rewritten retrieval query"], 1, True)

    monkeypatch.setattr(main, "AgenticRAG", FakeAgent)
    try:
        with TestClient(main.app) as client:
            paper = client.post(
                "/api/papers/upload", headers=headers("alice"),
                files={"files": ("agent.txt", b"retrieval evidence improves accuracy", "text/plain")},
            ).json()[0]["paper"]
            response = client.post(
                "/api/search", headers=headers("alice"),
                json={"query": "retrieval accuracy", "paper_ids": [paper["id"]]},
            )

        assert response.status_code == 200
        assert response.json()["generation_mode"] == "agentic_rag"
        assert response.json()["model"] == "gpt-5.4-nano"
        assert response.json()["retrieval_queries"] == ["rewritten retrieval query"]
        assert response.json()["grounded"] is True
        assert response.json()["fallback_reason"] is None
        assert response.json()["citations"][0]["page"] == 1
    finally:
        main.app.dependency_overrides.clear()


def test_search_injects_only_bounded_relevant_durable_memory(tmp_path, monkeypatch):
    store = setup_app(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(main, "embed_texts", lambda texts, **kwargs: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(main, "_agentic_dependencies_available", lambda: True)
    monkeypatch.setattr(main, "_build_agentic_chat_model", lambda: object())
    captured = {}

    class FakeAgent:
        def __init__(self, model, retrieve, **kwargs):
            self.retrieve = retrieve

        def run(self, query, *, memory="", deadline=None):
            captured["memory"] = memory
            citations = self.retrieve(query, 5)
            return AgenticRAGResult(
                "根拠付き回答です [1]", citations[:1], [query], 1, True,
                grounding_status="verified",
            )

    monkeypatch.setattr(main, "AgenticRAG", FakeAgent)
    try:
        with TestClient(main.app) as client:
            paper = client.post(
                "/api/papers/upload", headers=headers("alice"),
                files={"files": ("memory-agent.txt", b"chaos synchronization evidence", "text/plain")},
            ).json()[0]["paper"]
            conversation = client.post(
                "/api/research/conversations", headers=headers("alice"), json={"title": "Memory"},
            ).json()
            _, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
            store.add_research_exchange(
                workspace.id, conversation["id"], "仮説", "検証済み回答", [],
                memory_delta={"hypotheses": ["カオス同期は予測精度を改善する"]},
            )
            response = client.post(
                "/api/search", headers=headers("alice"),
                json={
                    "query": "カオス同期の仮説を続けて",
                    "paper_ids": [paper["id"]],
                    "conversation_id": conversation["id"],
                },
            )

        assert response.status_code == 200
        assert "カオス同期は予測精度を改善する" in captured["memory"]
        assert "関連する長期研究メモリ" in captured["memory"]
        assert len(captured["memory"]) <= 5_000
    finally:
        main.app.dependency_overrides.clear()


def test_research_message_and_memory_page_endpoints_are_workspace_scoped(tmp_path):
    store = setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            created = client.post(
                "/api/research/conversations", headers=headers("alice"), json={"title": "Paged"},
            ).json()
            _, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
            store.add_research_exchange(
                workspace.id, created["id"], "質問", "回答", [],
                memory_delta={"planned_tests": ["再現実験を行う"]},
            )
            messages = client.get(
                f"/api/research/conversations/{created['id']}/messages?limit=1",
                headers=headers("alice"),
            )
            memory = client.get(
                f"/api/research/conversations/{created['id']}/memory?kind=planned_test",
                headers=headers("alice"),
            )
            outsider_messages = client.get(
                f"/api/research/conversations/{created['id']}/messages", headers=headers("bob"),
            )
            invalid_limit = client.get(
                f"/api/research/conversations/{created['id']}/memory?limit=201",
                headers=headers("alice"),
            )

        assert messages.status_code == 200
        assert len(messages.json()["items"]) == 1
        assert messages.json()["next_before_ordinal"] == 2
        assert memory.status_code == 200
        assert memory.json()["items"][0]["content"] == "再現実験を行う"
        assert outsider_messages.status_code == 404
        assert invalid_limit.status_code == 422
    finally:
        main.app.dependency_overrides.clear()


def test_search_reports_safe_model_failure_code(tmp_path, monkeypatch):
    setup_app(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(main, "embed_texts", lambda texts, **kwargs: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(main, "_agentic_dependencies_available", lambda: True)
    monkeypatch.setattr(main, "_build_agentic_chat_model", lambda: object())

    class MissingModelError(Exception):
        status_code = 404

    class FailingAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            raise MissingModelError("response body must not be exposed")

    monkeypatch.setattr(main, "AgenticRAG", FailingAgent)
    try:
        with TestClient(main.app) as client:
            paper = client.post(
                "/api/papers/upload", headers=headers("alice"),
                files={"files": ("model.txt", b"retrieval evidence", "text/plain")},
            ).json()[0]["paper"]
            response = client.post(
                "/api/search", headers=headers("alice"),
                json={"query": "retrieval evidence", "paper_ids": [paper["id"]]},
            )
            status = client.get("/api/llm/status", headers=headers("alice"))

        assert response.status_code == 200
        assert response.json()["generation_mode"] == "local_fallback"
        assert response.json()["fallback_reason"] == "model_not_found"
        assert "response body" not in response.text
        assert status.json()["last_failure_code"] == "model_not_found"
    finally:
        main._set_last_llm_failure(None)
        main.app.dependency_overrides.clear()


def test_search_stream_opens_before_running_blocking_generation():
    async def scenario():
        response = await main.answer_stream(
            SearchRequest(query="stream response"), object(), object()
        )
        iterator = response.body_iterator
        first = await anext(iterator)
        await iterator.aclose()
        return first

    assert asyncio.run(scenario()) == ": stream-open\n\n"
