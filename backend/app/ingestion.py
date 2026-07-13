from __future__ import annotations

import os
import logging
import threading
from typing import Callable

from .extraction import DocumentExtractor, ExtractionConfig
from .models import Paper
from .rag import chunk_pages, embedding_config
from .storage import OriginalStorage
from .store import PaperStore, ResourceConflictError


logger = logging.getLogger(__name__)


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
    heartbeat = threading.Thread(
        target=lambda: _heartbeat_loop(
            store, job_id, paper_id, claimed.attempts, interval, stopped,
        ),
        daemon=True,
    )
    heartbeat.start()
    active_extractor: DocumentExtractor | None = None
    try:
        # Keep every operation after the heartbeat starts inside this lifecycle
        # boundary so failures cannot leak the daemon heartbeat thread.
        paper = store.get(paper_id)
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
        embedding_provider, embedding_job_model = embedding_config()
        store.complete_ingestion(
            job_id, paper, result.pages, result.elements, claimed.attempts,
            embedding_model=embedding_job_model,
            embedding_provider=embedding_provider,
        )
    except Exception as exc:
        if active_extractor is not None:
            for asset_key in active_extractor.created_asset_keys:
                try:
                    storage.delete(asset_key)
                except Exception as cleanup_error:
                    logger.warning(
                        "Failed to clean derived ingestion asset: type=%s",
                        cleanup_error.__class__.__name__,
                    )
        store.fail_ingestion(job_id, paper_id, str(exc) or exc.__class__.__name__, claimed.attempts)
        raise
    finally:
        stopped.set()
        heartbeat.join(timeout=1)


def _heartbeat_loop(store: PaperStore, job_id: str, paper_id: str, expected_attempt: int, interval: int, stopped: threading.Event) -> None:
    while not stopped.wait(interval):
        try:
            if not store.heartbeat_ingestion_job(job_id, paper_id, expected_attempt):
                return
        except Exception as exc:
            logger.warning("Ingestion heartbeat failed: type=%s", exc.__class__.__name__)
            continue


EmbeddingFunction = Callable[[list[str], str], list[list[float]]]


def _openai_embedding_batch(texts: list[str], model: str) -> list[list[float]]:
    if model == "local-hash-v1":
        from .rag import embed_texts

        return embed_texts(texts, force_local=True)
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    response = OpenAI(
        api_key=api_key,
        timeout=float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "15")),
        max_retries=0,
    ).embeddings.create(model=model, input=texts)
    return [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]


def _embedding_failure_code(exc: BaseException, provider: str) -> str:
    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        return "api_key_missing"
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "authentication_failed"
    if status == 403:
        return "permission_denied"
    if status == 404:
        return "model_not_found"
    if status == 429:
        return "rate_limited"
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "api_timeout"
    if "connection" in name:
        return "network_error"
    if isinstance(exc, ResourceConflictError):
        return "lease_superseded"
    return "embedding_failed"


def process_embedding_job(
    store: PaperStore,
    embedding_job_id: str,
    *,
    embedder: EmbeddingFunction | None = None,
    batch_size: int | None = None,
) -> bool:
    """Claim and populate missing chunk embeddings idempotently.

    Provider errors are reduced to stable error codes in the database; exception
    messages are never persisted because they may echo document content.
    """
    max_attempts = int(os.getenv("EMBEDDING_MAX_ATTEMPTS", "3"))
    claimed = store.claim_embedding_job(
        embedding_job_id,
        max_attempts,
        int(os.getenv("EMBEDDING_LEASE_SECONDS", "300")),
    )
    if claimed is None:
        return False
    if embedder is not None:
        active_embedder = embedder
    elif claimed.provider == "openai":
        active_embedder = _openai_embedding_batch
    elif claimed.provider == "local":
        active_embedder = lambda texts, _: _openai_embedding_batch(texts, "local-hash-v1")
    else:
        active_embedder = lambda *_: (_ for _ in ()).throw(RuntimeError("unsupported embedding provider"))
    size = max(1, min(batch_size or int(os.getenv("EMBEDDING_BATCH_SIZE", "64")), 128))
    try:
        paper = store.get(claimed.paper_id)
        chunks = paper.chunks
        existing = store.get_chunk_embeddings(
            claimed.workspace_id, [chunk.id for chunk in chunks], claimed.model,
        )
        missing = [chunk for chunk in chunks if chunk.id not in existing]
        completed = len(chunks) - len(missing)
        store.update_embedding_progress(claimed.id, completed, claimed.attempts)
        for offset in range(0, len(missing), size):
            batch = missing[offset: offset + size]
            vectors = active_embedder([chunk.text for chunk in batch], claimed.model)
            if len(vectors) != len(batch) or any(not vector for vector in vectors):
                raise RuntimeError("embedding provider returned an incomplete batch")
            store.upsert_chunk_embeddings(
                claimed.workspace_id, claimed.model,
                {chunk.id: vector for chunk, vector in zip(batch, vectors)},
            )
            completed += len(batch)
            if not store.update_embedding_progress(claimed.id, completed, claimed.attempts):
                raise ResourceConflictError("embedding lease was superseded")
        if not store.complete_embedding_job(claimed.id, claimed.attempts):
            raise ResourceConflictError("embedding lease was superseded")
        return True
    except BaseException as exc:
        store.fail_embedding_job(
            claimed.id, claimed.attempts, _embedding_failure_code(exc, claimed.provider), max_attempts,
        )
        raise
