from __future__ import annotations

import os
import threading

from .extraction import DocumentExtractor, ExtractionConfig
from .models import Paper
from .rag import chunk_pages
from .storage import OriginalStorage
from .store import PaperStore


class IngestionLeaseBusy(RuntimeError):
    pass


def process_ingestion_job(store: PaperStore, storage: OriginalStorage, job_id: str, paper_id: str, extractor: DocumentExtractor | None = None) -> None:
    config = extractor.config if extractor else ExtractionConfig.from_env()
    lease_seconds = int(os.getenv("INGESTION_LEASE_SECONDS", "300"))
    claimed = store.claim_ingestion_job(job_id, paper_id, int(os.getenv("INGESTION_MAX_ATTEMPTS", "3")), lease_seconds)
    if claimed is None:
        paper = store.get(paper_id)
        job = store.get_ingestion_job(paper.workspace_id, job_id)
        if job.status == "running": raise IngestionLeaseBusy("ingestion lease is active")
        return
    stopped = threading.Event()
    interval = max(1, int(os.getenv("INGESTION_HEARTBEAT_SECONDS", str(max(1, lease_seconds // 3)))))
    heartbeat = threading.Thread(target=lambda: _heartbeat_loop(store, job_id, paper_id, claimed.attempts, interval, stopped), daemon=True)
    heartbeat.start()
    paper = store.get(paper_id)
    try:
        if not paper.storage_key:
            raise RuntimeError("original file is not available")
        content = storage.path_for(paper.storage_key).read_bytes()
        if len(content) > int(os.getenv("INGESTION_MAX_INPUT_BYTES", str(25 * 1024 * 1024))):
            raise RuntimeError("ingestion input size limit exceeded")
        active_extractor = extractor or DocumentExtractor(config)
        result = active_extractor.extract(content, paper.storage_key, paper.id, storage)
        store.update_ingestion_progress(job_id, 80, claimed.attempts)
        page_pairs = [(page.page, page.text) for page in result.pages]
        paper.chunks = chunk_pages(page_pairs, paper.id)
        if not paper.chunks:
            raise RuntimeError("本文を抽出できませんでした")
        for page in result.pages:
            page.chunks = [chunk for chunk in paper.chunks if chunk.page == page.page]
            page.elements = [element for element in result.elements if element.page == page.page]
        paper.abstract = paper.chunks[0].text[:500]
        paper.page_count = len(result.pages)
        if result.title:
            paper.title = result.title
        store.complete_ingestion(job_id, paper, result.pages, result.elements, claimed.attempts)
    except Exception as exc:
        if "active_extractor" in locals():
            for asset_key in active_extractor.created_asset_keys:
                try: storage.delete(asset_key)
                except Exception: pass
        store.fail_ingestion(job_id, paper_id, str(exc) or exc.__class__.__name__, claimed.attempts)
        raise
    finally:
        stopped.set()
        heartbeat.join(timeout=1)


def _heartbeat_loop(store: PaperStore, job_id: str, paper_id: str, expected_attempt: int, interval: int, stopped: threading.Event) -> None:
    while not stopped.wait(interval):
        try:
            if not store.heartbeat_ingestion_job(job_id, paper_id, expected_attempt): return
        except Exception:
            continue
