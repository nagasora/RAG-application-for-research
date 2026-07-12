from __future__ import annotations

import os
from pathlib import Path

from celery import Celery

from .ingestion import IngestionLeaseBusy, process_ingestion_job
from .storage import LocalOriginalStorage
from .store import PaperStore


celery_app = Celery(
    "paperpilot",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
)
SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", os.getenv("INGESTION_MAX_SECONDS", "300")))
HARD_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", str(SOFT_TIME_LIMIT + 30)))
celery_app.conf.update(task_track_started=True, task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1, task_soft_time_limit=SOFT_TIME_LIMIT, task_time_limit=HARD_TIME_LIMIT)
celery_app.conf.beat_schedule = {
    "reap-stale-ingestion-jobs": {
        "task": "paperpilot.reap_ingestion_jobs",
        "schedule": int(os.getenv("INGESTION_REAPER_INTERVAL_SECONDS", "60")),
    }
}


@celery_app.task(bind=True, max_retries=2, name="paperpilot.ingest", soft_time_limit=SOFT_TIME_LIMIT, time_limit=HARD_TIME_LIMIT)
def ingest_task(self, paper_id: str, job_id: str) -> None:
    """Queue payload intentionally contains identifiers only."""
    try:
        store = PaperStore(os.environ["DATABASE_URL"])
        original_root = Path(os.getenv("PAPER_ORIGINAL_STORAGE_DIR", os.getenv("PAPER_STORAGE_DIR", "./data/originals")))
        asset_root = Path(os.getenv("PAPER_ASSET_STORAGE_DIR", "./data/assets"))
        process_ingestion_job(store, LocalOriginalStorage(original_root, asset_root), job_id, paper_id)
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
