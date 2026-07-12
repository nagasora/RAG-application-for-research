import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, IngestionJobRecord, PaperRecord
from app.models import Paper, Principal
from app.rag import chunk_pages
from app.store import DuplicatePaperError, PaperStore, ResourceConflictError


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
