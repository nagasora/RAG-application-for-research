import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, IngestionJobRecord, PaperRecord
from app.models import Paper, Principal
from app.rag import chunk_pages
from app.store import DuplicatePaperError, PaperNotFoundError, PaperStore, ResourceConflictError


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    Base.metadata.create_all(engine)
    return PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))


def identity(store, subject="u"):
    return store.ensure_user(Principal(issuer="test", subject=subject))


def processing_paper(store, content: bytes = b"paper", subject: str = "u") -> Paper:
    user, workspace = identity(store, subject)
    return Paper(
        user_id=subject,
        workspace_id=workspace.id,
        created_by=user.id,
        title="Study",
        status="processing",
        content_hash=hashlib.sha256(content).hexdigest(),
    )


def test_processing_to_ready_is_persisted_with_chunks(store):
    paper = processing_paper(store)
    store.begin_processing(paper)
    assert store.list(paper.workspace_id)[0].status == "processing"

    paper.chunks = chunk_pages([(2, "A grounded result.")], paper.id)
    paper.page_count = 2
    store.mark_ready(paper)

    saved = store.list(paper.workspace_id)[0]
    assert saved.status == "ready"
    assert saved.chunks[0].page == 2


def test_failed_state_is_persisted(store):
    paper = processing_paper(store)
    store.begin_processing(paper)
    failed = store.mark_failed(paper.id, "cannot parse")
    assert failed.status == "failed"
    assert failed.error_message == "cannot parse"


def test_content_hash_is_unique_per_workspace(store):
    first = processing_paper(store)
    store.begin_processing(first)

    duplicate = processing_paper(store)
    with pytest.raises(DuplicatePaperError) as error:
        store.begin_processing(duplicate)
    assert error.value.paper.id == first.id

    other_user = processing_paper(store, subject="other")
    store.begin_processing(other_user)
    assert len(store.list(other_user.workspace_id)) == 1

    user, _ = identity(store)
    second_workspace = store.create_workspace(user.id, "Second")
    same_user_other_workspace = Paper(
        user_id="u",
        workspace_id=second_workspace.id,
        created_by=user.id,
        title="Study",
        status="processing",
        content_hash=first.content_hash,
    )
    store.begin_processing(same_user_other_workspace)
    assert len(store.list(second_workspace.id)) == 1


def test_chunk_embedding_upsert_updates_atomically_on_sqlite(store):
    paper = processing_paper(store, content=b"embedding-upsert")
    store.begin_processing(paper)
    paper.chunks = chunk_pages([(1, "retrieval evidence")], paper.id)
    paper.page_count = 1
    store.mark_ready(paper)
    chunk_id = paper.chunks[0].id

    store.upsert_chunk_embeddings(paper.workspace_id, "model-a", {chunk_id: [1.0, 0.0]})
    store.upsert_chunk_embeddings(paper.workspace_id, "model-b", {chunk_id: [0.0, 1.0, 0.0]})

    assert store.get_chunk_embeddings(paper.workspace_id, [chunk_id], "model-a") == {}
    assert store.get_chunk_embeddings(paper.workspace_id, [chunk_id], "model-b") == {
        chunk_id: [0.0, 1.0, 0.0]
    }


def test_research_exchange_appends_to_latest_persisted_summary(store):
    user, workspace = identity(store)
    conversation = store.create_conversation(workspace.id, user.id, "Theory")

    store.add_research_exchange(workspace.id, conversation.id, "first hypothesis", "first result", [])
    store.add_research_exchange(workspace.id, conversation.id, "second hypothesis", "second result", [])

    detail = store.get_conversation(workspace.id, conversation.id)
    assert "first hypothesis" in detail.summary
    assert "second hypothesis" in detail.summary
    assert [message.role for message in detail.messages] == [
        "user", "assistant", "user", "assistant"
    ]


def test_research_memory_is_append_only_deduplicated_and_source_linked(store):
    user, workspace = identity(store)
    conversation = store.create_conversation(workspace.id, user.id, "Theory")
    first_delta = {
        "hypotheses": ["A causes B", "  A   causes B  "],
        "assumptions": ["C is controlled"],
        "unresolved_questions": ["Does D moderate B?"],
        "planned_tests": ["Run an ablation"],
    }
    store.add_research_exchange(
        workspace.id, conversation.id, "develop theory", "grounded answer", [], first_delta,
    )
    store.add_research_exchange(
        workspace.id, conversation.id, "continue", "next answer", [],
        {"hypotheses": ["a causes b"], "planned_tests": ["Run a replication"]},
    )

    detail = store.get_conversation(workspace.id, conversation.id)
    assert detail.message_count == 4
    assert detail.memory_event_count == 5
    assert [message.ordinal for message in detail.messages] == [1, 2, 3, 4]

    memory = store.list_research_memory_page(workspace.id, conversation.id)
    assert [item.ordinal for item in memory.items] == [1, 2, 3, 4, 5]
    assert {item.kind for item in memory.items} == {
        "hypothesis", "assumption", "unresolved_question", "planned_test"
    }
    assert all(item.source_message_id for item in memory.items)
    assert [item.content for item in memory.items if item.kind == "hypothesis"] == ["A causes B"]


def test_research_message_and_memory_cursor_pages_are_stable_and_workspace_scoped(store):
    user, workspace = identity(store)
    conversation = store.create_conversation(workspace.id, user.id, "Paged")
    for index in range(3):
        store.add_research_exchange(
            workspace.id, conversation.id, f"q{index}", f"a{index}", [],
            {"planned_tests": [f"test {index}"]},
        )

    newest = store.list_research_messages_page(workspace.id, conversation.id, limit=2)
    assert [item.ordinal for item in newest.items] == [5, 6]
    assert newest.next_before_ordinal == 5
    middle = store.list_research_messages_page(
        workspace.id, conversation.id, limit=2,
        before_ordinal=newest.next_before_ordinal,
    )
    assert [item.ordinal for item in middle.items] == [3, 4]
    assert middle.next_before_ordinal == 3
    oldest = store.list_research_messages_page(
        workspace.id, conversation.id, limit=2,
        before_ordinal=middle.next_before_ordinal,
    )
    assert [item.ordinal for item in oldest.items] == [1, 2]
    assert oldest.next_before_ordinal is None

    latest_memory = store.list_research_memory_page(workspace.id, conversation.id, limit=2)
    assert [item.ordinal for item in latest_memory.items] == [2, 3]
    assert latest_memory.next_before_ordinal == 2

    _, other_workspace = identity(store, "other")
    with pytest.raises(PaperNotFoundError):
        store.list_research_messages_page(other_workspace.id, conversation.id)
    with pytest.raises(PaperNotFoundError):
        store.list_research_memory_page(other_workspace.id, conversation.id)


def test_research_memory_search_is_relevant_bounded_recent_and_workspace_scoped(store):
    user, workspace = identity(store)
    conversation = store.create_conversation(workspace.id, user.id, "Memory retrieval")
    memories = [
        "Transformer attention improves retrieval",
        "Survey participant recruitment is incomplete",
        "Compare sparse retrieval with dense retrieval",
        "Retrieval quality needs a held-out evaluation",
    ]
    for index, memory in enumerate(memories):
        store.add_research_exchange(
            workspace.id, conversation.id, f"q{index}", f"a{index}", [],
            {"planned_tests": [memory]},
        )

    matches = store.search_research_memory(
        workspace.id, conversation.id, "retrieval quality", limit=2,
    )
    assert len(matches) == 2
    assert matches[0].content == "Retrieval quality needs a held-out evaluation"
    assert all("retrieval" in item.content.casefold() for item in matches)

    recent = store.search_research_memory(workspace.id, conversation.id, "", limit=2)
    assert [item.ordinal for item in recent] == [4, 3]

    _, other_workspace = identity(store, "memory-search-other")
    with pytest.raises(PaperNotFoundError):
        store.search_research_memory(other_workspace.id, conversation.id, "retrieval")


def test_conversation_detail_is_bounded_to_latest_one_hundred_messages(store):
    user, workspace = identity(store)
    conversation = store.create_conversation(workspace.id, user.id, "Long running")
    for index in range(51):
        store.add_research_exchange(workspace.id, conversation.id, f"q{index}", f"a{index}", [])

    detail = store.get_conversation(workspace.id, conversation.id)
    assert detail.message_count == 102
    assert len(detail.messages) == 100
    assert [detail.messages[0].ordinal, detail.messages[-1].ordinal] == [3, 102]


def test_record_research_exchange_does_not_reload_conversation_detail(store, monkeypatch):
    user, workspace = identity(store)
    conversation = store.create_conversation(workspace.id, user.id, "Write-only path")

    def fail_detail_reload(*args, **kwargs):
        raise AssertionError("detail reload is unnecessary on the answer write path")

    monkeypatch.setattr(store, "get_conversation", fail_detail_reload)
    store.record_research_exchange(
        workspace.id, conversation.id, "question", "answer", [],
        memory_delta={"hypotheses": ["bounded persistence"]},
    )

    metadata = store.get_conversation_metadata(workspace.id, conversation.id)
    assert metadata.message_count == 2
    assert metadata.memory_event_count == 1
    assert len(store.list_research_messages_page(workspace.id, conversation.id).items) == 2


def test_ingestion_lease_rejects_fresh_running_and_reclaims_stale(store):
    paper = processing_paper(store)
    store.begin_processing(paper)
    job = store.create_ingestion_job(paper.workspace_id, paper.id)
    first = store.claim_ingestion_job(job.id, paper.id, max_attempts=3, lease_seconds=60)
    assert first and first.attempts == 1
    assert store.claim_ingestion_job(job.id, paper.id, 3, 60) is None

    with store.session_factory.begin() as session:
        session.get(IngestionJobRecord, job.id).updated_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        session.get(PaperRecord, paper.id).status = "failed"
    reclaimed = store.claim_ingestion_job(job.id, paper.id, 3, 60)
    assert reclaimed and reclaimed.status == "running" and reclaimed.attempts == 2
    assert store.heartbeat_ingestion_job(job.id, paper.id, expected_attempt=1) is False
    assert store.heartbeat_ingestion_job(job.id, paper.id, expected_attempt=2) is True
    assert store.get(paper.id).status == "processing"


def test_reaper_fails_stale_job_at_max_attempts(store):
    paper = processing_paper(store, content=b"max-attempt")
    store.begin_processing(paper)
    job = store.create_ingestion_job(paper.workspace_id, paper.id)
    with store.session_factory.begin() as session:
        record = session.get(IngestionJobRecord, job.id)
        record.status, record.attempts = "running", 3
        record.updated_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    assert store.reap_ingestion_jobs(lease_seconds=60, max_attempts=3) == []
    terminal = store.get_ingestion_job(paper.workspace_id, job.id)
    assert terminal.status == "failed"
    assert store.get(paper.id).status == "failed"
    assert store.update_ingestion_progress(job.id, 50, 3) is False
    assert store.heartbeat_ingestion_job(job.id, paper.id, 3) is False
    assert store.fail_ingestion(job.id, paper.id, "late failure", 3) is False
    with pytest.raises(ResourceConflictError):
        store.complete_ingestion(job.id, store.get(paper.id), [], [], 3)


def test_reaper_requeues_stale_below_max_but_ignores_fresh(store):
    stale_paper = processing_paper(store, content=b"stale")
    store.begin_processing(stale_paper)
    stale_job = store.create_ingestion_job(stale_paper.workspace_id, stale_paper.id)
    fresh_paper = processing_paper(store, content=b"fresh")
    store.begin_processing(fresh_paper)
    fresh_job = store.create_ingestion_job(fresh_paper.workspace_id, fresh_paper.id)
    with store.session_factory.begin() as session:
        stale = session.get(IngestionJobRecord, stale_job.id)
        stale.status, stale.attempts = "running", 2
        stale.updated_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        fresh = session.get(IngestionJobRecord, fresh_job.id)
        fresh.status, fresh.attempts, fresh.updated_at = "running", 1, datetime.now(timezone.utc)
    queued = store.reap_ingestion_jobs(lease_seconds=60, max_attempts=3)
    assert queued == [(stale_paper.id, stale_job.id)]
    assert store.get_ingestion_job(stale_paper.workspace_id, stale_job.id).status == "queued"
    assert store.get_ingestion_job(fresh_paper.workspace_id, fresh_job.id).status == "running"


def test_reaper_fences_old_worker_before_and_after_replacement_claim(store):
    paper = processing_paper(store, content=b"fenced")
    store.begin_processing(paper)
    job = store.create_ingestion_job(paper.workspace_id, paper.id)
    old = store.claim_ingestion_job(job.id, paper.id, 3, 60)
    with store.session_factory.begin() as session:
        session.get(IngestionJobRecord, job.id).updated_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    assert store.reap_ingestion_jobs(60, 3) == [(paper.id, job.id)]

    # Reaper changed status to queued: the old attempt is fenced even before replacement claim.
    assert store.update_ingestion_progress(job.id, 40, old.attempts) is False
    assert store.heartbeat_ingestion_job(job.id, paper.id, old.attempts) is False
    assert store.fail_ingestion(job.id, paper.id, "old failure", old.attempts) is False
    with pytest.raises(ResourceConflictError):
        store.complete_ingestion(job.id, store.get(paper.id), [], [], old.attempts)

    replacement = store.claim_ingestion_job(job.id, paper.id, 3, 60)
    assert replacement.attempts == old.attempts + 1
    assert store.update_ingestion_progress(job.id, 50, old.attempts) is False
    assert store.heartbeat_ingestion_job(job.id, paper.id, old.attempts) is False
    assert store.fail_ingestion(job.id, paper.id, "old failure", old.attempts) is False
    with pytest.raises(ResourceConflictError):
        store.complete_ingestion(job.id, store.get(paper.id), [], [], old.attempts)

    assert store.update_ingestion_progress(job.id, 80, replacement.attempts) is True
    store.complete_ingestion(job.id, store.get(paper.id), [], [], replacement.attempts)
    assert store.get_ingestion_job(paper.workspace_id, job.id).status == "succeeded"
    assert store.get(paper.id).status == "ready"
    assert store.fail_ingestion(job.id, paper.id, "old after success", old.attempts) is False
    assert store.update_ingestion_progress(job.id, 90, old.attempts) is False
    assert store.heartbeat_ingestion_job(job.id, paper.id, old.attempts) is False
    with pytest.raises(ResourceConflictError):
        store.complete_ingestion(job.id, store.get(paper.id), [], [], old.attempts)
