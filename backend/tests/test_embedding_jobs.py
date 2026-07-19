import hashlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.database import ChunkEmbeddingRecord, ChunkRecord
from app.celery_app import EMBEDDING_QUEUE, celery_app, embed_chunks_task
from app.ingestion import process_embedding_job
from app.models import Chunk, Paper, Principal
from app.rag import hybrid_search
from app.store import PaperStore, ResourceConflictError


def make_store(tmp_path) -> PaperStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'embeddings.db'}")
    Base.metadata.create_all(engine)
    return PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))


def completed_ingestion(store: PaperStore, *, content: bytes = b"embedding-paper"):
    user, workspace = store.ensure_user(Principal(issuer="test", subject="researcher"))
    paper = Paper(
        user_id="researcher", workspace_id=workspace.id, created_by=user.id,
        title="Embedding study", status="processing",
        content_hash=hashlib.sha256(content).hexdigest(),
    )
    store.begin_processing(paper)
    ingestion_job = store.create_ingestion_job(workspace.id, paper.id)
    claimed = store.claim_ingestion_job(ingestion_job.id, paper.id, 3)
    paper.chunks = [
        Chunk(paper_id=paper.id, page=1, text="first chunk"),
        Chunk(paper_id=paper.id, page=2, text="second chunk"),
        Chunk(paper_id=paper.id, page=3, text="third chunk"),
    ]
    paper.page_count = 3
    store.complete_ingestion(
        ingestion_job.id, paper, [], [], claimed.attempts,
        embedding_model="test-embedding-model",
    )
    dispatched = store.reap_embedding_jobs(120, 3)
    assert len(dispatched) == 1
    return paper, store.get_embedding_job(dispatched[0])


def test_ingestion_transaction_creates_idempotent_batched_embedding_job(tmp_path):
    store = make_store(tmp_path)
    paper, job = completed_ingestion(store)
    calls: list[list[str]] = []

    def embed(texts, model):
        assert model == "test-embedding-model"
        calls.append(texts)
        return [[float(len(text)), 1.0] for text in texts]

    assert job.status == "queued" and job.total_chunks == 3
    assert job.provider == "openai"
    assert store.embedding_statuses(paper.workspace_id, [paper.id], job.model) == {
        paper.id: "queued"
    }
    assert process_embedding_job(store, job.id, embedder=embed, batch_size=2) is True

    finished = store.get_embedding_job(job.id)
    saved = store.get_chunk_embeddings(
        paper.workspace_id, [chunk.id for chunk in paper.chunks], job.model,
    )
    assert [len(batch) for batch in calls] == [2, 1]
    assert finished.status == "succeeded"
    assert finished.progress == 100 and finished.completed_chunks == 3
    assert len(saved) == 3
    assert process_embedding_job(store, job.id, embedder=lambda *_: pytest.fail("must not rerun")) is False


def test_embedding_retry_preserves_completed_cache_and_uses_safe_error_code(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    paper, job = completed_ingestion(store, content=b"retry-paper")
    first_chunk = paper.chunks[0]
    store.upsert_chunk_embeddings(
        paper.workspace_id, job.model, {first_chunk.id: [1.0, 0.0]},
    )
    monkeypatch.setenv("EMBEDDING_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with pytest.raises(TimeoutError, match="provider timeout details"):
        process_embedding_job(
            store, job.id,
            embedder=lambda *_: (_ for _ in ()).throw(TimeoutError("provider timeout details")),
        )

    retryable = store.get_embedding_job(job.id)
    assert retryable.status == "queued"
    assert retryable.error_code == "api_timeout"
    assert retryable.completed_chunks == 1

    seen: list[list[str]] = []

    def succeed(texts, model):
        seen.append(texts)
        return [[0.0, 1.0] for _ in texts]

    assert process_embedding_job(store, job.id, embedder=succeed) is True
    assert seen == [["second chunk", "third chunk"]]
    assert store.get_embedding_job(job.id).attempts == 2


def test_dispatch_reservation_prevents_duplicate_queue_delivery(tmp_path):
    store = make_store(tmp_path)
    _, job = completed_ingestion(store, content=b"dispatch-paper")

    # completed_ingestion already reserved this queued job for dispatch.
    assert store.reap_embedding_jobs(120, 3) == []
    assert store.get_embedding_job(job.id).error_code == "dispatched"


def test_embedding_worker_has_dedicated_queue_and_periodic_dispatch():
    assert EMBEDDING_QUEUE == "embedding"
    assert embed_chunks_task.name == "paperpilot.embed_chunks"
    assert embed_chunks_task.queue == "embedding"
    schedule = celery_app.conf.beat_schedule["dispatch-embedding-jobs"]
    assert schedule["task"] == "paperpilot.dispatch_embedding_jobs"
    assert schedule["schedule"] == 10


def test_local_provider_does_not_require_openai_key(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    paper, job = completed_ingestion(store, content=b"local-provider")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Explicit provider selection is persisted independently of key visibility in
    # the ingestion worker. Requeue the fixture job as a local job for execution.
    with store.session_factory.begin() as session:
        from app.database import EmbeddingJobRecord
        record = session.get(EmbeddingJobRecord, job.id)
        record.provider = "local"
        record.model = "local-hash-v1"
        record.error_code = None
    assert process_embedding_job(store, job.id) is True
    assert len(store.get_chunk_embeddings(
        paper.workspace_id, [chunk.id for chunk in paper.chunks], "local-hash-v1"
    )) == 3


def test_ready_papers_are_backfilled_for_the_active_provider_model(tmp_path):
    store = make_store(tmp_path)
    paper, old_job = completed_ingestion(store, content=b"provider-backfill")
    # A completed prior identity does not block a deliberate provider backfill.
    with store.session_factory.begin() as session:
        from app.database import EmbeddingJobRecord
        session.get(EmbeddingJobRecord, old_job.id).status = "succeeded"
    assert store.ensure_embedding_jobs("local", "local-hash-v1") == 1
    assert store.ensure_embedding_jobs("local", "local-hash-v1") == 0
    assert store.embedding_statuses(
        paper.workspace_id, [paper.id], "local-hash-v1",
    ) == {paper.id: "queued"}


def test_reindex_never_allows_a_provider_switch_to_overwrite_a_running_job(tmp_path):
    store = make_store(tmp_path)
    paper, local_job = completed_ingestion(store, content=b"reindex-race")
    with store.session_factory.begin() as session:
        from app.database import EmbeddingJobRecord
        record = session.get(EmbeddingJobRecord, local_job.id)
        record.provider = "local"
        record.model = "local-hash-v1"
        record.status = "running"
        record.attempts = 1

    with pytest.raises(ResourceConflictError):
        store.reindex_embedding_jobs(paper.workspace_id, "openai", "text-embedding-3-small")

    assert store.get_embedding_job(local_job.id).status == "running"
    assert store.get_chunk_embeddings(
        paper.workspace_id, [chunk.id for chunk in paper.chunks], "text-embedding-3-small",
    ) == {}


def test_claim_then_provider_reindex_uses_the_same_paper_first_fencing_boundary(tmp_path):
    store = make_store(tmp_path)
    paper, local_job = completed_ingestion(store, content=b"claim-reindex-order")
    with store.session_factory.begin() as session:
        from app.database import EmbeddingJobRecord
        record = session.get(EmbeddingJobRecord, local_job.id)
        record.provider = "local"
        record.model = "local-hash-v1"
        record.status = "queued"
        record.error_code = None

    # claim_embedding_job locks Paper before the provider-specific job.  A
    # following reindex observes the committed running job and safely fences.
    assert store.claim_embedding_job(local_job.id, max_attempts=3) is not None
    with pytest.raises(ResourceConflictError):
        store.reindex_embedding_jobs(paper.workspace_id, "openai", "text-embedding-3-small")


def test_reindex_supersedes_queued_old_provider_before_new_vectors_can_be_written(tmp_path):
    store = make_store(tmp_path)
    paper, local_job = completed_ingestion(store, content=b"reindex-queued")
    with store.session_factory.begin() as session:
        from app.database import EmbeddingJobRecord
        record = session.get(EmbeddingJobRecord, local_job.id)
        record.provider = "local"
        record.model = "local-hash-v1"
        record.status = "queued"
        record.error_code = None

    jobs = store.reindex_embedding_jobs(paper.workspace_id, "openai", "text-embedding-3-small")
    assert len(jobs) == 1 and jobs[0].provider == "openai"
    old = store.get_embedding_job(local_job.id)
    assert old.status == "failed" and old.error_code == "superseded_by_reindex"
    assert process_embedding_job(store, old.id, embedder=lambda *_: pytest.fail("superseded job must not run")) is False
    assert process_embedding_job(
        store, jobs[0].id, embedder=lambda texts, _: [[2.0, 0.0] for _ in texts],
    ) is True
    saved = store.get_chunk_embeddings(
        paper.workspace_id, [chunk.id for chunk in paper.chunks], "text-embedding-3-small",
    )
    assert len(saved) == len(paper.chunks)
    # Mocked multilingual query vector: lexical Japanese terms do not occur in
    # this English chunk, yet its OpenAI-style vector is retrieved semantically.
    hit = hybrid_search([store.get(paper.id)], "因果関係の検索", saved, [2.0, 0.0], limit=1)
    assert hit and hit[0][1].text == "first chunk"


def test_reindex_same_identity_invalidates_cache_and_regenerates_every_chunk(tmp_path):
    store = make_store(tmp_path)
    paper, job = completed_ingestion(store, content=b"same-identity-reindex")
    assert process_embedding_job(
        store, job.id, embedder=lambda texts, _: [[1.0, 0.0] for _ in texts],
    ) is True

    rebuilt = store.reindex_embedding_jobs(paper.workspace_id, job.provider, job.model)
    assert len(rebuilt) == 1
    assert store.get_chunk_embeddings(
        paper.workspace_id, [chunk.id for chunk in paper.chunks], job.model,
    ) == {}

    calls: list[list[str]] = []

    def regenerate(texts, model):
        assert model == job.model
        calls.append(texts)
        return [[0.0, 1.0] for _ in texts]

    assert process_embedding_job(store, rebuilt[0].id, embedder=regenerate) is True
    assert calls == [["first chunk", "second chunk", "third chunk"]]
    assert store.get_chunk_embeddings(
        paper.workspace_id, [chunk.id for chunk in paper.chunks], job.model,
    ) == {chunk.id: [0.0, 1.0] for chunk in paper.chunks}


def test_reindex_only_invalidates_embeddings_for_the_requested_model_before_rebuild(tmp_path):
    store = make_store(tmp_path)
    paper, job = completed_ingestion(store, content=b"model-scoped-reindex")
    store.upsert_chunk_embeddings(
        paper.workspace_id, job.model,
        {chunk.id: [1.0, 0.0] for chunk in paper.chunks[1:]},
    )
    store.upsert_chunk_embeddings(
        paper.workspace_id, "archived-model", {paper.chunks[0].id: [0.0, 1.0]},
    )

    store.reindex_embedding_jobs(paper.workspace_id, job.provider, job.model)

    with store.session_factory() as session:
        remaining = session.execute(
            select(ChunkEmbeddingRecord.chunk_id, ChunkEmbeddingRecord.model)
            .join_from(ChunkEmbeddingRecord, ChunkRecord)
            .where(ChunkRecord.paper_id == paper.id)
        ).all()
    assert remaining == [(paper.chunks[0].id, "archived-model")]
