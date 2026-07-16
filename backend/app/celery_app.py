from __future__ import annotations

import os
from celery import Celery

from .ingestion import IngestionLeaseBusy, process_embedding_job, process_ingestion_job
from .rag import embedding_config
from .storage import storage_from_environment
from .store import PaperStore


celery_app = Celery(
    "paperpilot",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
)
SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", os.getenv("INGESTION_MAX_SECONDS", "300")))
HARD_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", str(SOFT_TIME_LIMIT + 30)))
EMBEDDING_SOFT_TIME_LIMIT = int(os.getenv("EMBEDDING_TASK_SOFT_TIME_LIMIT", "180"))
EMBEDDING_HARD_TIME_LIMIT = int(os.getenv("EMBEDDING_TASK_TIME_LIMIT", str(EMBEDDING_SOFT_TIME_LIMIT + 30)))
EMBEDDING_QUEUE = os.getenv("EMBEDDING_TASK_QUEUE", "embedding")
celery_app.conf.update(task_track_started=True, task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1, task_soft_time_limit=SOFT_TIME_LIMIT, task_time_limit=HARD_TIME_LIMIT)
celery_app.conf.beat_schedule = {
    "reap-stale-ingestion-jobs": {
        "task": "paperpilot.reap_ingestion_jobs",
        "schedule": int(os.getenv("INGESTION_REAPER_INTERVAL_SECONDS", "60")),
    },
    "dispatch-embedding-jobs": {
        "task": "paperpilot.dispatch_embedding_jobs",
        "schedule": int(os.getenv("EMBEDDING_DISPATCH_INTERVAL_SECONDS", "10")),
    },
}


@celery_app.task(bind=True, max_retries=2, name="paperpilot.ingest", soft_time_limit=SOFT_TIME_LIMIT, time_limit=HARD_TIME_LIMIT)
def ingest_task(self, paper_id: str, job_id: str) -> None:
    """Queue payload intentionally contains identifiers only."""
    try:
        store = PaperStore(os.environ["DATABASE_URL"])
        process_ingestion_job(store, storage_from_environment(), job_id, paper_id)
    except IngestionLeaseBusy as exc:
        raise self.retry(exc=exc, countdown=int(os.getenv("INGESTION_LEASE_SECONDS", "300")) + 1)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=min(60, 2 ** self.request.retries))


def enqueue_ingestion(paper_id: str, job_id: str) -> None:
    ingest_task.delay(paper_id, job_id)


@celery_app.task(name="paperpilot.reap_ingestion_jobs")
def reap_ingestion_jobs_task() -> int:
    store = PaperStore(os.environ["DATABASE_URL"])
    jobs = store.reap_ingestion_jobs(
        int(os.getenv("INGESTION_LEASE_SECONDS", "300")),
        int(os.getenv("INGESTION_MAX_ATTEMPTS", "3")),
    )
    for paper_id, job_id in jobs:
        enqueue_ingestion(paper_id, job_id)
    return len(jobs)


@celery_app.task(
    bind=True,
    max_retries=2,
    name="paperpilot.embed_chunks",
    queue=EMBEDDING_QUEUE,
    soft_time_limit=EMBEDDING_SOFT_TIME_LIMIT,
    time_limit=EMBEDDING_HARD_TIME_LIMIT,
)
def embed_chunks_task(self, embedding_job_id: str) -> None:
    """Embedding queue payload contains only an opaque job identifier."""
    store = PaperStore(os.environ["DATABASE_URL"])
    try:
        process_embedding_job(store, embedding_job_id)
    except Exception:
        # Provider exception messages can contain request data; keep Celery's
        # persisted retry exception deliberately generic.
        raise self.retry(
            exc=RuntimeError("embedding job failed"),
            countdown=min(60, 2 ** self.request.retries),
        ) from None


def enqueue_embedding(embedding_job_id: str) -> None:
    embed_chunks_task.apply_async(args=[embedding_job_id], queue=EMBEDDING_QUEUE)


@celery_app.task(name="paperpilot.dispatch_embedding_jobs")
def dispatch_embedding_jobs_task() -> int:
    """Transactional DB jobs are the source of truth; Redis delivery is repeatable."""
    store = PaperStore(os.environ["DATABASE_URL"])
    provider, model = embedding_config()
    store.ensure_embedding_jobs(provider, model)
    job_ids = store.reap_embedding_jobs(
        int(os.getenv("EMBEDDING_LEASE_SECONDS", "300")),
        int(os.getenv("EMBEDDING_MAX_ATTEMPTS", "3")),
        int(os.getenv("EMBEDDING_DISPATCH_BATCH_SIZE", "100")),
    )
    for job_id in job_ids:
        enqueue_embedding(job_id)
    return len(job_ids)
