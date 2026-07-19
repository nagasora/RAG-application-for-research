from __future__ import annotations

import asyncio
import io
import csv
import hashlib
import importlib.util
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Callable, Literal
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pypdf import PdfReader

from .auth import get_current_principal
from .agentic_rag import AgenticRAG
from .models import (
    AnalysisRequest, Chunk, ComparisonRow, EmbeddingJobStatus, EmbeddingReindexRequest, EmbeddingReindexResponse, ExternalPaperRequest, LLMStatus, OperationsStatus, MeResponse, Paper, PaperDetail, PaperMarkdownSummary,
    DocumentElement, IngestionJob, Note, NoteCreate, NoteUpdate, PaperDecision, PaperDecisionUpdate, PaperLibraryPage, PaperPage, PaperSummary, PaperTagsBulkUpdate, PaperTagsUpdate, Principal,
    Citation, ResearchGap, SavedComparison, SavedComparisonCreate, SearchHistory, SearchPreviewResponse, SearchRequest,
    SearchResponse, Tag, TagCreate, UploadResult, User, Workspace, WorkspaceCreate,
    ResearchQuestion, ResearchQuestionCreate, ResearchQuestionUpdate, SourceSet, SourceSetCreate, SourceSetUpdate,
    ResearchRun, ResearchRunCreate, RunArtifact, RunArtifactCreate,
    ResearchConversation, ResearchConversationCreate, ResearchConversationDetail,
    ResearchMemoryPage, ResearchMessagePage,
    WorkspaceMember, WorkspaceMemberCreate, WorkspaceMemberUpdate,
    CanvasLayout, CanvasLayoutUpdate, ForwardPropagationCreate, ForwardPropagationResult, GraphRetrieveRequest, GraphRetrievalHit,
    GraphSnapshot, KnowledgeEdge, KnowledgeEdgeCreate, KnowledgeEdgeStatusUpdate, KnowledgeNode,
    KnowledgeNodeCreate, KnowledgeNodeStatusResult, KnowledgeNodeStatusUpdate, NodeFeedback,
    NodeFeedbackCreate, ReasoningRun, ReasoningRunCreate, SourceSpan,
    SourceImportCreate, SourceImportResult, SourceVersion, SourceVersionCreate,
    HypothesisCard, HypothesisCardCreate, HypothesisCardStatusUpdate,
    DiscoveryItem, DiscoveryItemCreate, DiscoveryReviewUpdate,
    BeliefEvent, BeliefEventCreate, ExperimentPlan, ExperimentPlanCreate, ExperimentPlanSnapshot, ExperimentResultCreate, Idea, IdeaCreate, IdeaUpdate,
    ConversationGraphExportCreate, GraphIdeaCandidate, ReviewAssignmentUpdate, ReviewCandidate, ReviewCommentCreate, ReviewDecisionCreate, ReviewThread, ReviewThreadCreate,
)
from .graph_rag import GraphEdge as RetrievalGraphEdge, PrunedTwoHopConfig, RetrievalSeed, pruned_two_hop_retrieve
from .source_parsers import SourceParseLimitError, parse_source
from .rag import (
    ANSWER_MODEL, chunk_pages, citations_from, compare_papers, embed_texts, embedding_config, embedding_model, extractive_answer,
    hybrid_search, reciprocal_rank_fusion, research_gaps, search,
)
from .ingestion import process_embedding_job, process_ingestion_job
from .storage import ImmutableObjectExists, LocalOriginalStorage, OriginalStorage, storage_from_environment
from .store import (
    DuplicatePaperError, PaperNotFoundError, PaperStore, ResourceConflictError,
    WorkspaceAccessError, WorkspaceMemberConflictError, WorkspaceMemberNotFoundError,
    WorkspacePermissionError,
)

load_dotenv()

logger = logging.getLogger("paperpilot.rag")

FallbackReason = Literal[
    "api_key_missing", "dependency_missing", "no_evidence", "grounding_failed",
    "authentication_failed", "permission_denied", "model_not_found", "rate_limited",
    "api_timeout", "network_error", "model_api_error", "deadline_exceeded",
    "model_timeout", "model_unavailable", "provider_unavailable", "model_call_failed",
    "generation_failed", "citation_validation_failed", "grounding_audit_failed", "repair_failed",
    "structured_output_invalid", "verification_skipped_timeout",
]
_last_llm_failure: FallbackReason | None = None
_last_llm_failure_lock = Lock()


def run_background_ingestion(
    store: PaperStore,
    originals: OriginalStorage,
    job_id: str,
    paper_id: str,
) -> None:
    """Run a locally queued ingestion job without holding the upload request open.

    This is deliberately a small deployment-mode bridge rather than a replacement
    for Celery.  The job/paper state is committed before this function is queued,
    so a browser can reliably poll ``/api/jobs/{job_id}`` even when PDF extraction
    takes longer than a reverse-proxy request timeout.  ``process_ingestion_job``
    persists the failure state itself; logging here keeps a useful server-side
    traceback without leaking an exception back through an already-completed
    upload response.
    """
    try:
        process_ingestion_job(store, originals, job_id, paper_id)
    except Exception:
        logger.exception(
            "Paper ingestion failed in background: paper_id=%s job_id=%s",
            paper_id,
            job_id,
        )


def _agentic_dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(module) is not None
        for module in ("langchain_core", "langchain_openai", "langchain_text_splitters")
    )


def _set_last_llm_failure(code: FallbackReason | None) -> None:
    global _last_llm_failure
    with _last_llm_failure_lock:
        _last_llm_failure = code


def _get_last_llm_failure() -> FallbackReason | None:
    with _last_llm_failure_lock:
        return _last_llm_failure


def _classify_llm_failure(exc: Exception) -> FallbackReason:
    if isinstance(exc, ModuleNotFoundError):
        return "dependency_missing"
    status_code = getattr(exc, "status_code", None)
    if status_code == 401:
        return "authentication_failed"
    if status_code == 403:
        return "permission_denied"
    if status_code == 404:
        return "model_not_found"
    if status_code == 429:
        return "rate_limited"
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "api_timeout"
    if "connection" in name:
        return "network_error"
    return "model_api_error"


def _build_agentic_chat_model(timeout_seconds: float = 18.0):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=ANSWER_MODEL,
        api_key=os.environ["OPENAI_API_KEY"],
        # Respect the Agentic RAG stage budget. Keep the initial client usable
        # for the 16-second generation stage; _ask still supplies a smaller
        # timeout for planning and verification calls.
        timeout=max(0.5, min(timeout_seconds, 20.0)),
        # The request-level deadline owns retries. SDK retries can otherwise
        # consume the whole response budget before a local fallback is returned.
        max_retries=0,
        # This is only an upper bound, not a reserved/charged token count.
        # Complex Japanese answers with LaTeX can exceed 1,800 tokens and leave
        # Structured Outputs as truncated, invalid JSON.
        max_tokens=4000,
        use_responses_api=True,
    )


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _cors_origins_from_environment() -> list[str]:
    """Return exact browser origins accepted by the API.

    Deployment dashboards often copy a site URL with a trailing slash, while
    browsers send ``Origin`` without one.  Treat comma-separated values as a
    small explicit allow-list and normalize only that harmless difference.
    """
    configured = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
    origins = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return origins or ["http://localhost:3000"]


def _local_forward_hypothesis(input_contents: list[str], evidence_texts: list[str]) -> str:
    """Produce a transparent offline candidate when an LLM is unavailable."""
    premises = " / ".join(content.strip() for content in input_contents if content.strip())
    evidence = " ".join(text.strip() for text in evidence_texts if text.strip())
    return (
        f"仮説（要レビュー）: {premises} を前提に、観測された根拠"
        f"（{evidence[:500] or '選択された原典'}）と整合する検証可能な効果が得られる。"
    )


def _generate_forward_hypothesis(input_contents: list[str], evidence_texts: list[str], prompt: str) -> tuple[str, dict]:
    """Generate a falsifiable hypothesis with a deterministic local fallback."""
    if not os.getenv("OPENAI_API_KEY"):
        return _local_forward_hypothesis(input_contents, evidence_texts), {
            "generation_mode": "local_fallback", "model": None, "fallback_reason": "api_key_missing",
        }
    try:
        from openai import OpenAI

        premises = "\n".join(f"- {content[:4_000]}" for content in input_contents if content.strip())
        evidence = "\n".join(f"- {text[:4_000]}" for text in evidence_texts if text.strip())
        response = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"], timeout=20.0, max_retries=0,
        ).responses.create(
            model=ANSWER_MODEL, store=False,
            instructions=(
                "あなたは根拠に基づく研究仮説を作るアシスタントです。与えられた前提と原典だけを使い、"
                "一文から三文の検証可能で反証可能な仮説を日本語で返してください。根拠にない数値や事実は追加しないでください。"
            ),
            input=f"前提ノード:\n{premises}\n\n原典根拠:\n{evidence}\n\n追加指示:\n{prompt}",
        )
        content = response.output_text.strip()
        if not content:
            raise RuntimeError("empty hypothesis response")
        _set_last_llm_failure(None)
        return content, {"generation_mode": "llm", "model": ANSWER_MODEL, "fallback_reason": None}
    except Exception as exc:
        reason = _classify_llm_failure(exc)
        _set_last_llm_failure(reason)
        logger.warning("forward_propagation_fallback code=%s exception=%s", reason, exc.__class__.__name__)
        return _local_forward_hypothesis(input_contents, evidence_texts), {
            "generation_mode": "local_fallback", "model": None, "fallback_reason": reason,
        }


MAX_UPLOAD_FILES = _positive_int_env("MAX_UPLOAD_FILES", 10)
RAG_REQUEST_DEADLINE_SECONDS = _positive_float_env("RAG_REQUEST_DEADLINE_SECONDS", 25.0)
PAPER_SUMMARY_DEADLINE_SECONDS = _positive_float_env("PAPER_SUMMARY_DEADLINE_SECONDS", 16.0)
MAX_UPLOAD_BYTES = _positive_int_env("MAX_UPLOAD_BYTES", 25 * 1024 * 1024)
MAX_PDF_PAGES = _positive_int_env("MAX_PDF_PAGES", 300)
READ_CHUNK_BYTES = 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=4)
def _store_for_url(database_url: str) -> PaperStore:
    return PaperStore(database_url)


def _sqlalchemy_database_url(database_url: str) -> str:
    """Accept the standard PostgreSQL URL exposed by managed providers.

    Render Postgres exposes ``postgresql://`` URLs, while this application uses
    SQLAlchemy with psycopg 3 and therefore needs the explicit
    ``postgresql+psycopg://`` dialect.  Keep already-explicit URLs unchanged so
    local and other managed deployments retain their configured driver.
    """
    if database_url.startswith("postgresql://"):
        return f"postgresql+psycopg://{database_url.removeprefix('postgresql://')}"
    return database_url


def get_store() -> PaperStore:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    return _store_for_url(_sqlalchemy_database_url(database_url))


@lru_cache(maxsize=4)
def _storage_for_roots(original_root: str, asset_root: str) -> LocalOriginalStorage:
    return LocalOriginalStorage(Path(original_root), Path(asset_root))


def get_original_storage() -> OriginalStorage:
    # Local storage keeps the existing root-specific cache.  Remote object
    # storage deliberately owns its own client and disposable read-through
    # cache, configured by PAPER_STORAGE_BACKEND=r2 (or s3).
    if os.getenv("PAPER_STORAGE_BACKEND", "local").strip().lower() == "local":
        legacy = os.getenv("PAPER_STORAGE_DIR")
        original = Path(os.getenv("PAPER_ORIGINAL_STORAGE_DIR", legacy or "./data/originals"))
        assets = Path(os.getenv("PAPER_ASSET_STORAGE_DIR", "./data/assets" if not legacy else legacy))
        original_root = original if original.is_absolute() else ROOT / original
        asset_root = assets if assets.is_absolute() else ROOT / assets
        return _storage_for_roots(str(original_root.resolve()), str(asset_root.resolve()))
    return storage_from_environment(base_dir=ROOT)


@dataclass(frozen=True)
class CurrentUser:
    user: User
    personal_workspace: Workspace


@dataclass(frozen=True)
class WorkspaceContext:
    user: User
    workspace: Workspace


def get_current_user(
    principal: Principal = Depends(get_current_principal),
    store: PaperStore = Depends(get_store),
) -> CurrentUser:
    try:
        user, personal = store.ensure_user(principal)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="identity store is unavailable") from exc
    return CurrentUser(user=user, personal_workspace=personal)


def get_workspace_context(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> WorkspaceContext:
    requested = request.headers.get("X-Workspace-ID", "").strip() or None
    try:
        workspace = store.resolve_workspace(current.user.id, requested)
    except WorkspaceAccessError as exc:
        # Do not reveal whether an inaccessible workspace exists.
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    return WorkspaceContext(user=current.user, workspace=workspace)


def require_workspace_write(context: WorkspaceContext) -> None:
    if context.workspace.role not in {"owner", "editor"}:
        raise HTTPException(status_code=403, detail="workspace write access is required")

app = FastAPI(title="PaperPilot API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins_from_environment(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def summary(paper: Paper) -> PaperSummary:
    return PaperSummary(
        **paper.model_dump(exclude={"chunks", "content_hash", "storage_key", "mime_type", "byte_size"}),
        chunk_count=len(paper.chunks),
    )


def detail(paper: Paper) -> PaperDetail:
    return PaperDetail(
        **summary(paper).model_dump(),
        storage_key=paper.storage_key,
        mime_type=paper.mime_type,
        byte_size=paper.byte_size,
    )


async def read_upload_limited(file: UploadFile) -> bytes:
    content = bytearray()
    while chunk := await file.read(READ_CHUNK_BYTES):
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"ファイルサイズ上限 {MAX_UPLOAD_BYTES} bytes を超えています")
    return bytes(content)


@app.get("/api/health")
def health(store: PaperStore = Depends(get_store)) -> dict[str, str]:
    try:
        store.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "ok"}


@app.get("/api/operations/status", response_model=OperationsStatus)
def operations_status(current: CurrentUser = Depends(get_current_user)) -> OperationsStatus:
    """Configuration-only operational readiness; never probes Redis or providers."""
    mode = os.getenv("INGESTION_MODE", "inline").strip().lower()
    mode = mode if mode in {"inline", "celery"} else "inline"
    configured = bool(os.getenv("CELERY_BROKER_URL")) and bool(os.getenv("CELERY_RESULT_BACKEND"))
    warnings = []
    if mode == "celery" and not configured:
        warnings.append("celery mode requires broker and result backend URLs")
    if mode != "celery":
        warnings.append("Celery is required before production asynchronous ingestion")
    return OperationsStatus(ingestion_mode=mode, celery_required=True, celery_configured=configured,
        retry_limit=int(os.getenv("INGESTION_MAX_ATTEMPTS", "3")), embedding_retry_limit=int(os.getenv("EMBEDDING_MAX_ATTEMPTS", "3")),
        backup_restore_runbook="docs/OPERATIONS_RUNBOOK.md#backup-restore", ci017_outbox_note="CI-017 partial failures require compensating storage actions and queued-job reaping.", warnings=warnings)


@app.get("/api/me", response_model=MeResponse)
def me(current: CurrentUser = Depends(get_current_user)) -> MeResponse:
    return MeResponse(user=current.user, personal_workspace=current.personal_workspace)


@app.get("/api/llm/status", response_model=LLMStatus)
def llm_status(current: CurrentUser = Depends(get_current_user)) -> LLMStatus:
    del current  # Authentication is the purpose of this dependency.
    return LLMStatus(
        configured=bool(os.getenv("OPENAI_API_KEY")),
        model=ANSWER_MODEL,
        embedding_model=embedding_model(),
        agentic_dependencies_available=_agentic_dependencies_available(),
        last_failure_code=_get_last_llm_failure(),
    )


def _embedding_status(job) -> EmbeddingJobStatus:
    return EmbeddingJobStatus(
        id=job.id, paper_id=job.paper_id, provider=job.provider, model=job.model,
        status=job.status, progress=job.progress, attempts=job.attempts,
        total_chunks=job.total_chunks, completed_chunks=job.completed_chunks,
        error_code=job.error_code, created_at=job.created_at, updated_at=job.updated_at,
    )


@app.post("/api/embeddings/reindex", response_model=EmbeddingReindexResponse)
def reindex_embeddings(
    body: EmbeddingReindexRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> EmbeddingReindexResponse:
    """Rebuild active-workspace vectors without exposing provider credentials."""
    require_workspace_write(context)
    provider, model = embedding_config()
    try:
        jobs = store.reindex_embedding_jobs(context.workspace.id, provider, model, body.paper_ids)
    except PaperNotFoundError as exc:
        # Keep the workspace boundary opaque: an ID in another workspace is
        # indistinguishable from an absent or non-ready paper.
        raise HTTPException(status_code=404, detail="ready paper not found") from exc
    except ResourceConflictError as exc:
        raise HTTPException(status_code=409, detail="embedding is already running; retry after it completes") from exc
    mode = os.getenv("INGESTION_MODE", "inline").strip().lower()
    if mode == "celery":
        from .celery_app import enqueue_embedding
        for job in jobs:
            if job.status == "queued":
                enqueue_embedding(job.id)
    else:
        # Local Compose has no worker.  Process the durable jobs in this request
        # so the user can switch embedding providers and ask in the next turn.
        for job in jobs:
            if job.status == "queued":
                try:
                    process_embedding_job(store, job.id)
                except Exception:
                    logger.warning("Embedding reindex failed: job_id=%s", job.id)
        jobs = [store.get_embedding_job(job.id) for job in jobs]
        mode = "inline"
    return EmbeddingReindexResponse(provider=provider, model=model, mode=mode, jobs=[_embedding_status(job) for job in jobs])


@app.get("/api/workspaces", response_model=list[Workspace])
def list_workspaces(
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> list[Workspace]:
    return store.list_workspaces(current.user.id)


@app.post("/api/workspaces", response_model=Workspace, status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> Workspace:
    return store.create_workspace(current.user.id, body.name)


@app.get("/api/workspaces/{workspace_id}", response_model=Workspace)
def get_workspace(
    workspace_id: str,
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> Workspace:
    """Resolve a switch target without exposing inaccessible workspaces."""
    try:
        return store.resolve_workspace(current.user.id, workspace_id)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc


@app.patch("/api/workspaces/{workspace_id}", response_model=Workspace)
def rename_workspace(
    workspace_id: str,
    body: WorkspaceCreate,
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> Workspace:
    try:
        return store.rename_workspace(current.user.id, workspace_id, body.name)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except WorkspacePermissionError as exc:
        raise HTTPException(status_code=403, detail="workspace owner role is required") from exc


def _require_member_workspace_access(store: PaperStore, user_id: str, workspace_id: str) -> Workspace:
    """Resolve path-scoped workspaces without leaking inaccessible workspace IDs."""
    try:
        return store.resolve_workspace(user_id, workspace_id)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc


@app.get("/api/workspaces/{workspace_id}/members", response_model=list[WorkspaceMember])
def list_workspace_members(
    workspace_id: str,
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> list[WorkspaceMember]:
    _require_member_workspace_access(store, current.user.id, workspace_id)
    return store.list_workspace_members(workspace_id)


@app.post("/api/workspaces/{workspace_id}/members", response_model=WorkspaceMember, status_code=201)
def add_workspace_member(
    workspace_id: str,
    body: WorkspaceMemberCreate,
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> WorkspaceMember:
    try:
        return store.add_workspace_member_for_owner(
            current.user.id, workspace_id, issuer=current.user.issuer,
            email=body.email, subject=body.subject, role=body.role,
        )
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except WorkspacePermissionError as exc:
        raise HTTPException(status_code=403, detail="workspace owner role is required") from exc
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="member was not found; they must sign in once before being added",
        ) from exc
    except WorkspaceMemberConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/workspaces/{workspace_id}/members/{member_user_id}", response_model=WorkspaceMember)
def update_workspace_member(
    workspace_id: str,
    member_user_id: str,
    body: WorkspaceMemberUpdate,
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> WorkspaceMember:
    try:
        return store.update_workspace_member_for_owner(
            current.user.id, workspace_id, member_user_id, role=body.role,
        )
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace member not found") from exc
    except WorkspacePermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.delete("/api/workspaces/{workspace_id}/members/{member_user_id}", status_code=204)
def remove_workspace_member(
    workspace_id: str,
    member_user_id: str,
    current: CurrentUser = Depends(get_current_user),
    store: PaperStore = Depends(get_store),
) -> Response:
    try:
        store.remove_workspace_member_for_owner(current.user.id, workspace_id, member_user_id)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace member not found") from exc
    except WorkspacePermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return Response(status_code=204)


@app.get("/api/research/questions", response_model=list[ResearchQuestion])
def list_research_questions(
    store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context),
) -> list[ResearchQuestion]:
    return store.list_research_questions(context.workspace.id)


@app.post("/api/research/questions", response_model=ResearchQuestion, status_code=201)
def create_research_question(
    body: ResearchQuestionCreate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> ResearchQuestion:
    require_workspace_write(context)
    return store.create_research_question(
        context.workspace.id, context.user.id, title=body.title, question=body.question,
    )


@app.get("/api/research/questions/{question_id}", response_model=ResearchQuestion)
def get_research_question(
    question_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> ResearchQuestion:
    try:
        return store.get_research_question(context.workspace.id, question_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research question not found") from exc


@app.patch("/api/research/questions/{question_id}", response_model=ResearchQuestion)
def update_research_question(
    question_id: str, body: ResearchQuestionUpdate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> ResearchQuestion:
    require_workspace_write(context)
    try:
        return store.update_research_question(
            context.workspace.id, question_id, title=body.title, question=body.question,
        )
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research question not found") from exc


@app.delete("/api/research/questions/{question_id}", status_code=204)
def delete_research_question(
    question_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> Response:
    require_workspace_write(context)
    if not store.delete_research_question(context.workspace.id, question_id):
        raise HTTPException(status_code=404, detail="research question not found")
    return Response(status_code=204)


@app.get("/api/source-sets", response_model=list[SourceSet])
def list_source_sets(
    store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context),
) -> list[SourceSet]:
    return store.list_source_sets(context.workspace.id)


@app.post("/api/source-sets", response_model=SourceSet, status_code=201)
def create_source_set(
    body: SourceSetCreate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> SourceSet:
    require_workspace_write(context)
    try:
        return store.create_source_set(
            context.workspace.id, context.user.id, name=body.name,
            description=body.description, paper_ids=body.paper_ids,
        )
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="paper not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/source-sets/{source_set_id}", response_model=SourceSet)
def get_source_set(
    source_set_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> SourceSet:
    try:
        return store.get_source_set(context.workspace.id, source_set_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="source set not found") from exc


@app.patch("/api/source-sets/{source_set_id}", response_model=SourceSet)
def update_source_set(
    source_set_id: str, body: SourceSetUpdate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> SourceSet:
    require_workspace_write(context)
    try:
        return store.update_source_set(
            context.workspace.id, source_set_id, name=body.name,
            description=body.description, paper_ids=body.paper_ids,
        )
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="source set or paper not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/source-sets/{source_set_id}", status_code=204)
def delete_source_set(
    source_set_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> Response:
    require_workspace_write(context)
    if not store.delete_source_set(context.workspace.id, source_set_id):
        raise HTTPException(status_code=404, detail="source set not found")
    return Response(status_code=204)


@app.get("/api/ideas", response_model=list[Idea])
def list_ideas(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_ideas(context.workspace.id)

@app.post("/api/ideas", response_model=Idea, status_code=201)
def create_idea(body: IdeaCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.create_idea(context.workspace.id, context.user.id, body)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="idea anchor not found") from exc
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.patch("/api/ideas/{idea_id}", response_model=Idea)
def update_idea(idea_id: str, body: IdeaUpdate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.update_idea(context.workspace.id, idea_id, body)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="idea or anchor not found") from exc
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.post("/api/ideas/{idea_id}/promote", response_model=Idea)
def promote_idea(idea_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.promote_idea(context.workspace.id, context.user.id, idea_id)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="idea not found") from exc
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/reviews", response_model=list[ReviewThread])
def list_review_threads(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_review_threads(context.workspace.id)


@app.get("/api/reviews/candidates", response_model=list[ReviewCandidate])
def list_review_candidates(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    """Claims persisted by Ask that may be chosen as review anchors."""
    return store.list_review_candidates(context.workspace.id)


@app.get("/api/research/conversations/{conversation_id}/messages/{message_id}/graph-candidates", response_model=list[GraphIdeaCandidate])
def list_graph_idea_candidates(
    conversation_id: str, message_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return store.list_graph_idea_candidates(context.workspace.id, conversation_id, message_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research conversation or assistant message not found") from exc


@app.post("/api/research/conversations/{conversation_id}/messages/{message_id}/graph-drafts", response_model=list[KnowledgeNode], status_code=201)
def export_conversation_graph_drafts(
    conversation_id: str, message_id: str, body: ConversationGraphExportCreate,
    store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.export_conversation_graph_drafts(
            context.workspace.id, context.user.id, conversation_id, message_id, body,
        )
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research conversation, message, or source span not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/reviews", response_model=ReviewThread, status_code=201)
def create_review_thread(body: ReviewThreadCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.create_review_thread(context.workspace.id, context.user.id, body)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="review anchor or assignee not found") from exc
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/reviews/report.md")
def export_review_report(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return Response(
        content=store.export_review_report_markdown(context.workspace.id),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="paperpilot-review-report.md"'},
    )


@app.get("/api/reviews/{thread_id}", response_model=ReviewThread)
def get_review_thread(thread_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    try: return store.get_review_thread(context.workspace.id, thread_id)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="review thread not found") from exc


@app.patch("/api/reviews/{thread_id}/assignment", response_model=ReviewThread)
def assign_review_thread(thread_id: str, body: ReviewAssignmentUpdate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.assign_review_thread(context.workspace.id, thread_id, body.assigned_to)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="review thread or assignee not found") from exc


@app.post("/api/reviews/{thread_id}/comments", response_model=ReviewThread)
def add_review_comment(thread_id: str, body: ReviewCommentCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.add_review_comment(context.workspace.id, thread_id, context.user.id, body.body)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="review thread not found") from exc


@app.post("/api/reviews/{thread_id}/decisions", response_model=ReviewThread)
def add_review_decision(thread_id: str, body: ReviewDecisionCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.add_review_decision(
        context.workspace.id, thread_id, context.user.id,
        verdict=body.verdict, reason=body.reason,
    )
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="review thread not found") from exc

@app.get("/api/research/runs", response_model=list[ResearchRun])
def list_research_runs(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_research_runs(context.workspace.id)


@app.post("/api/research/runs", response_model=ResearchRun, status_code=201)
def create_research_run(
    body: ResearchRunCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.create_research_run(context.workspace.id, context.user.id, **body.model_dump())
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research question, source set, or paper not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/research/runs/{run_id}", response_model=ResearchRun)
def get_research_run(run_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    try:
        return store.get_research_run(context.workspace.id, run_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research run not found") from exc


@app.post("/api/research/runs/{run_id}/artifacts", response_model=RunArtifact, status_code=201)
def append_run_artifact(
    run_id: str, body: RunArtifactCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.append_run_artifact(context.workspace.id, run_id, kind=body.kind, payload=body.payload)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/research/runs/{run_id}/cancel", response_model=ResearchRun)
def cancel_research_run(run_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try:
        return store.cancel_research_run(context.workspace.id, run_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/papers", response_model=list[PaperSummary])
def list_papers(
    user_id: str | None = Query(default=None, deprecated=True),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    return [
        summary(paper)
        for paper in sorted(store.list(context.workspace.id), key=lambda p: p.created_at, reverse=True)
    ]


@app.get("/api/library/papers", response_model=PaperLibraryPage)
def list_library_papers(
    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100),
    query: str = Query(default="", max_length=500), status: str | None = None, source: str | None = None,
    tag_id: str | None = None, source_set_id: str | None = None,
    decision: Literal["undecided", "included", "excluded"] | None = None,
    store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context),
) -> PaperLibraryPage:
    try:
        return store.list_library_page(context.workspace.id, page=page, page_size=page_size, query=query, status=status, source=source, tag_id=tag_id, source_set_id=source_set_id, decision=decision)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="source set not found") from exc


@app.put("/api/papers/{paper_id}/decision", response_model=PaperDecision)
def set_paper_decision(
    paper_id: str, body: PaperDecisionUpdate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> PaperDecision:
    require_workspace_write(context)
    try:
        return store.update_paper_decision(context.workspace.id, paper_id, decision=body.decision, reason=body.reason)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="paper not found") from exc


@app.post("/api/papers/bulk/tags", status_code=204)
def bulk_update_paper_tags(
    body: PaperTagsBulkUpdate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> Response:
    require_workspace_write(context)
    try:
        store.bulk_update_paper_tags(context.workspace.id, body.paper_ids, body.tag_ids, operation=body.operation)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="paper or tag not found") from exc
    return Response(status_code=204)


@app.post("/api/papers/upload", response_model=list[UploadResult])
async def upload_papers(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user_id: str | None = Form(default=None),
    store: PaperStore = Depends(get_store),
    originals: OriginalStorage = Depends(get_original_storage),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    if len(files) > MAX_UPLOAD_FILES:
        message = f"一度にアップロードできるファイルは {MAX_UPLOAD_FILES} 件までです"
        return [UploadResult(filename=file.filename or "Untitled", success=False, status="rejected", error=message) for file in files]

    ingestion_mode = os.getenv("INGESTION_MODE", "inline").lower()
    results: list[UploadResult] = []
    for file in files:
        filename = file.filename or "Untitled"
        suffix_match = re.search(r"\.(pdf|txt|md)$", filename, flags=re.I)
        if not suffix_match:
            results.append(UploadResult(filename=filename, success=False, status="rejected", error="PDF / TXT / Markdown のみ対応しています"))
            continue
        try:
            content = await read_upload_limited(file)
            if not content:
                raise ValueError("空のファイルです")
        except Exception as exc:
            results.append(UploadResult(filename=filename, success=False, status="rejected", error=str(exc)))
            continue

        title = re.sub(r"\.(pdf|txt|md)$", "", filename, flags=re.I)
        content_hash = hashlib.sha256(content).hexdigest()
        suffix = suffix_match.group(1).lower()
        mime_type = {
            "pdf": "application/pdf",
            "txt": "text/plain; charset=utf-8",
            "md": "text/markdown; charset=utf-8",
        }[suffix]
        paper_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            user_id=context.user.subject,
            workspace_id=context.workspace.id,
            created_by=context.user.id,
            title=title,
            source="upload",
            status="processing",
            content_hash=content_hash,
            storage_key=f"originals/papers/{paper_id}/original.{suffix}",
            mime_type=mime_type,
            byte_size=len(content),
        )
        try:
            store.begin_processing(paper)
        except DuplicatePaperError as exc:
            results.append(UploadResult(
                filename=filename,
                success=True,
                status="duplicate",
                paper=summary(exc.paper),
                error="同じ内容の論文が既に登録されています",
                duplicate=True,
            ))
            continue
        except Exception as exc:
            results.append(UploadResult(filename=filename, success=False, status="rejected", error=f"登録開始に失敗しました: {exc}"))
            continue

        try:
            originals.put(paper.storage_key or "", content)
        except Exception as exc:
            message = f"原本保存に失敗しました: {str(exc) or exc.__class__.__name__}"
            try:
                failed = store.mark_failed(paper.id, message, clear_storage=True)
                failed_summary = summary(failed)
            except Exception:
                failed_summary = None
            results.append(UploadResult(
                filename=filename,
                success=False,
                status="failed",
                paper=failed_summary,
                error=message,
            ))
            continue

        try:
            job = store.create_ingestion_job(context.workspace.id, paper.id)
        except Exception as exc:
            message = f"ingestion job creation failed: {exc}"
            try:
                failed = store.mark_failed(paper.id, message)
                failed_summary = summary(failed)
                status = "failed"
            except Exception:
                # Do not report a durable failed paper if its failure state could
                # not be committed.  Best-effort compensation removes both the
                # unqueueable record and its freshly written original.
                try:
                    store.delete(context.workspace.id, paper.id)
                    if paper.storage_key:
                        originals.delete(paper.storage_key)
                except Exception:
                    logger.exception("Could not compensate failed ingestion job creation: paper_id=%s", paper.id)
                failed_summary = None
                status = "rejected"
            results.append(UploadResult(filename=filename, success=False, status=status, paper=failed_summary, error=message))
            continue
        if ingestion_mode == "celery":
            from .celery_app import enqueue_ingestion
            try:
                enqueue_ingestion(paper.id, job.id)
                results.append(UploadResult(filename=filename, success=True, status="processing", paper=summary(paper), job=job))
            except Exception as exc:
                message = f"ingestion queue unavailable: {exc}"
                store.abort_queued_ingestion(job.id, paper.id, message)
                results.append(UploadResult(filename=filename, success=False, status="failed", paper=summary(store.get(paper.id)), error=message, job=store.get_ingestion_job(context.workspace.id, job.id)))
            continue
        if ingestion_mode == "background":
            # FastAPI runs synchronous background work after the 202 response has
            # been sent.  Keep the durable job as the source of truth: clients
            # must poll it instead of treating the accepted upload as success.
            background_tasks.add_task(
                run_background_ingestion, store, originals, job.id, paper.id,
            )
            results.append(UploadResult(
                filename=filename,
                success=True,
                status="processing",
                paper=summary(paper),
                job=job,
            ))
            continue
        try:
            process_ingestion_job(store, originals, job.id, paper.id)
            ready = store.get(paper.id)
            results.append(UploadResult(filename=filename, success=True, status="ready", paper=summary(ready), job=store.get_ingestion_job(context.workspace.id, job.id)))
        except Exception as exc:
            failed = store.get(paper.id)
            results.append(UploadResult(filename=filename, success=False, status="failed", paper=summary(failed), error=str(exc) or exc.__class__.__name__, job=store.get_ingestion_job(context.workspace.id, job.id)))
    if ingestion_mode in {"celery", "background"}:
        return JSONResponse(status_code=202, content=[result.model_dump(mode="json") for result in results])
    return results


@app.post("/api/papers/external", response_model=PaperSummary)
def add_external_paper(
    body: ExternalPaperRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    title, authors, year, abstract = body.title, body.authors, body.year, body.abstract
    identifier = body.identifier.strip()
    try:
        arxiv_id = re.sub(r"^(arxiv:|https?://arxiv.org/abs/)", "", identifier, flags=re.I)
        if re.match(r"^\d{4}\.\d+(v\d+)?$", arxiv_id, re.I):
            response = httpx.get("https://export.arxiv.org/api/query", params={"id_list": arxiv_id}, timeout=15)
            response.raise_for_status()
            entry = ET.fromstring(response.text).find("{http://www.w3.org/2005/Atom}entry")
            if entry is not None:
                title = title or (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
                abstract = abstract or (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()
                authors = authors or [node.findtext("{http://www.w3.org/2005/Atom}name") or "" for node in entry.findall("{http://www.w3.org/2005/Atom}author")]
                published = entry.findtext("{http://www.w3.org/2005/Atom}published") or ""
                year = year or (int(published[:4]) if published[:4].isdigit() else None)
        elif identifier.lower().startswith("10."):
            response = httpx.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{identifier}",
                params={"fields": "title,authors,year,abstract"}, timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            title = title or data.get("title")
            abstract = abstract or data.get("abstract") or ""
            authors = authors or [author.get("name", "") for author in data.get("authors", [])]
            year = year or data.get("year")
    except (httpx.HTTPError, ET.ParseError, ValueError):
        pass
    title = title or f"External paper: {identifier}"
    paper = Paper(
        user_id=context.user.subject,
        workspace_id=context.workspace.id,
        created_by=context.user.id,
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        source="arXiv" if "arxiv" in body.identifier.lower() or re.match(r"^\d{4}\.\d+", body.identifier) else "DOI",
        external_id=identifier,
        page_count=1 if abstract else 0,
        content_hash=hashlib.sha256(f"external:{identifier.lower()}".encode("utf-8")).hexdigest(),
    )
    if abstract:
        paper.chunks = chunk_pages([(1, abstract)], paper.id)
    try:
        embedding_provider, configured_model = embedding_config()
        store.upsert(
            paper, embedding_model=configured_model, embedding_provider=embedding_provider,
        )
    except DuplicatePaperError as exc:
        return summary(exc.paper)
    return summary(paper)


@app.get("/api/papers/{paper_id}", response_model=PaperDetail)
def get_paper_detail(
    paper_id: str,
    user_id: str | None = Query(default=None, deprecated=True),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return detail(store.get_owned(context.workspace.id, paper_id))
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="論文が見つかりません") from exc


@app.get("/api/papers/{paper_id}/file")
def get_paper_file(
    paper_id: str,
    user_id: str | None = Query(default=None, deprecated=True),
    store: PaperStore = Depends(get_store),
    originals: OriginalStorage = Depends(get_original_storage),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        paper = store.get_owned(context.workspace.id, paper_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="論文が見つかりません") from exc
    if not paper.storage_key or not paper.mime_type:
        raise HTTPException(status_code=404, detail="原本ファイルがありません")
    try:
        path = originals.path_for(paper.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="原本ファイルが失われています") from exc
    extension = path.suffix
    return FileResponse(
        path,
        media_type=paper.mime_type,
        filename=f"{paper.title}{extension}",
        content_disposition_type="inline",
    )


@app.get("/api/papers/{paper_id}/pages/{page}", response_model=PaperPage)
def get_paper_page(
    paper_id: str,
    page: int,
    user_id: str | None = Query(default=None, deprecated=True),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be at least 1")
    try:
        return store.get_page_extraction(context.workspace.id, paper_id, page)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="ページが見つかりません") from exc


@app.get("/api/papers/{paper_id}/chunks/{chunk_id}", response_model=Chunk)
def get_paper_chunk(
    paper_id: str,
    chunk_id: str,
    user_id: str | None = Query(default=None, deprecated=True),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return store.get_chunk(context.workspace.id, paper_id, chunk_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="チャンクが見つかりません") from exc


@app.get("/api/jobs/{job_id}", response_model=IngestionJob)
def get_ingestion_job(job_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    try: return store.get_ingestion_job(context.workspace.id, job_id)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="job not found") from exc


@app.get("/api/papers/{paper_id}/assets", response_model=list[DocumentElement])
def get_paper_assets(paper_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    try: return store.list_document_elements(context.workspace.id, paper_id)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="paper not found") from exc


@app.get("/api/papers/{paper_id}/assets/{element_id}/file")
def get_figure_asset(
    paper_id: str,
    element_id: str,
    store: PaperStore = Depends(get_store),
    originals: OriginalStorage = Depends(get_original_storage),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        element = store.get_document_element(context.workspace.id, paper_id, element_id)
        if element.kind != "figure" or not element.asset_key:
            raise PaperNotFoundError(element_id)
        path = originals.path_for(element.asset_key)
    except (PaperNotFoundError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="asset not found") from None
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".tif": "image/tiff", ".tiff": "image/tiff"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=f"{element.id}{path.suffix.lower()}")


@app.delete("/api/papers/{paper_id}", status_code=204)
def delete_paper(
    paper_id: str,
    user_id: str | None = Query(default=None, deprecated=True),
    store: PaperStore = Depends(get_store),
    originals: OriginalStorage = Depends(get_original_storage),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        paper = store.get_owned(context.workspace.id, paper_id)
        extracted_assets = [element.asset_key for element in store.list_document_elements(context.workspace.id, paper_id) if element.asset_key]
    except PaperNotFoundError:
        raise HTTPException(status_code=404, detail="論文が見つかりません") from None
    storage_keys = [key for key in [paper.storage_key, *extracted_assets] if key]
    # Snapshot first, then delete the blobs before committing the DB deletion.
    # If either phase fails, compensate the blobs so a paper is never reported
    # deleted while its original/asset set is only partly removed.
    snapshots: list[tuple[str, bytes]] = []
    try:
        for key in storage_keys:
            try:
                snapshots.append((key, originals.path_for(key).read_bytes()))
            except FileNotFoundError:
                pass
        for key in storage_keys:
            originals.delete(key)
    except Exception as exc:
        for key, content in snapshots:
            try:
                originals.put(key, content)
            except ImmutableObjectExists:
                pass
            except Exception:
                logger.exception("Could not restore paper storage after failed delete: key=%s", key)
        raise HTTPException(status_code=503, detail="原本または抽出アセットを削除できなかったため、論文は保持されました") from exc
    try:
        deleted = store.delete(context.workspace.id, paper_id)
    except ResourceConflictError as exc:
        for key, content in snapshots:
            try:
                originals.put(key, content)
            except ImmutableObjectExists:
                pass
            except Exception:
                logger.exception("Could not restore paper storage after rejected delete: key=%s", key)
        raise HTTPException(
            status_code=409,
            detail="この論文は知識グラフの根拠として使用されているため削除できません",
        ) from exc
    except Exception as exc:
        for key, content in snapshots:
            try:
                originals.put(key, content)
            except ImmutableObjectExists:
                pass
            except Exception:
                logger.exception("Could not restore paper storage after failed DB delete: key=%s", key)
        raise HTTPException(status_code=500, detail="論文削除を完了できなかったため、原本を復元しました") from exc
    if not deleted:
        for key, content in snapshots:
            try:
                originals.put(key, content)
            except ImmutableObjectExists:
                pass
        raise HTTPException(status_code=404, detail="論文が見つかりません")


def filtered_papers(body: SearchRequest, store: PaperStore, workspace_id: str) -> list[Paper]:
    papers = store.list(workspace_id)
    papers = [paper for paper in papers if paper.status == "ready"]
    if body.paper_ids:
        papers = [p for p in papers if p.id in body.paper_ids]
    if body.year_from is not None:
        papers = [p for p in papers if p.year is None or p.year >= body.year_from]
    if body.year_to is not None:
        papers = [p for p in papers if p.year is None or p.year <= body.year_to]
    return papers


def _query_relevance(query: str, text: str) -> float:
    """Small deterministic seed scorer; graph expansion remains bounded later."""
    normalized_query, normalized_text = query.casefold().strip(), text.casefold()
    raw_terms = re.findall(r"[a-z0-9_]+|[一-龯ぁ-んァ-ヶ]{2,}", normalized_query)
    terms: list[str] = []
    for term in raw_terms:
        terms.append(term)
        if re.search(r"[一-龯ぁ-んァ-ヶ]", term) and len(term) > 2:
            terms.extend(term[index:index + 2] for index in range(len(term) - 1))
    terms = list(dict.fromkeys(terms))
    if not terms:
        return 0.0
    matched = sum(1 for term in terms if term in normalized_text)
    exact_bonus = 0.25 if normalized_query and normalized_query in normalized_text else 0.0
    return min(1.0, matched / len(terms) + exact_bonus)


def _best_span_chunk(paper: Paper, span_text: str, page: int | None) -> Chunk | None:
    """Resolve graph evidence to a real, clickable chunk on the same page."""
    candidates = [chunk for chunk in paper.chunks if page is None or chunk.page == page]
    if not candidates:
        return None
    span_terms = set(re.findall(r"[a-z0-9_]+|[一-龯ぁ-んァ-ヶ]{2,}", span_text.casefold()))

    def overlap(chunk: Chunk) -> tuple[float, int, str]:
        chunk_terms = set(re.findall(r"[a-z0-9_]+|[一-龯ぁ-んァ-ヶ]{2,}", chunk.text.casefold()))
        lexical = len(span_terms & chunk_terms) / max(1, len(span_terms))
        contains = 1.0 if span_text.strip() and span_text.strip() in chunk.text else 0.0
        return (contains + lexical, -abs(len(chunk.text) - len(span_text)), chunk.id)

    selected = max(candidates, key=overlap)
    # A page match alone is not evidence that this chunk contains the quoted
    # span.  Refuse to manufacture a clickable target when text overlap is zero.
    return selected if overlap(selected)[0] > 0 else None


def _paper_backed_graph_citation(
    store: PaperStore, workspace_id: str, allowed_papers: dict[str, Paper], *,
    evidence, source_kind: Literal["graph_node", "graph_edge"], score: float,
    knowledge_node_id: str | None = None, knowledge_edge_id: str | None = None,
    graph_path: list[dict] | None = None,
    retrieval_reason: str | None = None, retrieval_stance: str | None = None,
    source_versions: dict | None = None, source_spans: dict | None = None,
) -> Citation | None:
    """Convert only authorized, paper-backed immutable evidence to Citation."""
    try:
        version = (
            source_versions.get(evidence.source_version_id)
            if source_versions is not None else store.get_source_version(workspace_id, evidence.source_version_id)
        )
        if version is None:
            return None
        if not version.paper_id or version.paper_id not in allowed_papers:
            return None
        span = (
            source_spans.get(evidence.source_span_id)
            if source_spans is not None else store.get_source_span(workspace_id, evidence.source_span_id)
        )
        if span is None:
            return None
    except PaperNotFoundError:
        return None
    paper = allowed_papers[version.paper_id]
    quote = evidence.verbatim_quote or span.text
    chunk = _best_span_chunk(paper, quote, span.page)
    if chunk is None:
        return None
    return Citation(
        index=0, paper_id=paper.id, paper_title=paper.title, chunk_id=chunk.id,
        page=chunk.page, section=chunk.section, excerpt=chunk.text[:1_000],
        score=round(max(0.0, min(1.0, score)), 4), source_kind=source_kind,
        source_version_id=version.id, source_span_id=span.id,
        evidence_role=evidence.role,
        knowledge_node_id=knowledge_node_id, knowledge_edge_id=knowledge_edge_id,
        graph_path=list(graph_path or []),
        extraction_quality=evidence.extraction_quality,
        retrieval_reason=retrieval_reason, source_quote=quote,
        retrieval_stance=retrieval_stance,
    )


def _graph_citation_candidates(
    store: PaperStore, workspace_id: str, query: str, papers: list[Paper], limit: int,
    *, paper_ids: list[str] | None = None, year_from: int | None = None,
    year_to: int | None = None,
) -> dict[str, list[tuple[str, Citation]]]:
    """Retrieve workspace-scoped graph evidence via lexical seeds and two hops."""
    allowed_papers = {paper.id: paper for paper in papers}
    if hasattr(store, "retrieve_knowledge_subgraph"):
        graph_nodes, graph_edges = store.retrieve_knowledge_subgraph(
            workspace_id, query, seed_limit=12, edge_limit=200, evidence_limit=400,
        )
    else:
        graph_nodes = store.list_knowledge_nodes(workspace_id)
        graph_edges = store.list_knowledge_edges(workspace_id)
    retrievable = {
        node.id: node for node in graph_nodes if node.status in {"active", "verified"}
    }
    seed_scores = [
        (node_id, _query_relevance(query, " ".join([
            node.content,
            *(item.target_claim for item in node.evidence),
            *(item.verbatim_quote for item in node.evidence),
        ])))
        for node_id, node in retrievable.items()
    ]
    seeds = [
        RetrievalSeed(node_id=node_id, relevance=score, confidence=1.0 if node.confidence is None else node.confidence,
                      retrieval_reason="query_graph_seed")
        for node_id, score in sorted(seed_scores, key=lambda item: (-item[1], item[0]))[:12]
        if score > 0 for node in [retrievable[node_id]]
    ]
    if not seeds:
        return {"graph_nodes": [], "graph_contradicts": []}
    edges = [
        edge for edge in graph_edges
        if edge.status in {"active", "verified"}
        and edge.source_node_id in retrievable and edge.target_node_id in retrievable
    ]
    outgoing: dict[str, list[RetrievalGraphEdge]] = {}
    for edge in edges:
        try:
            confidence = float(edge.metadata.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        outgoing.setdefault(edge.source_node_id, []).append(RetrievalGraphEdge(
            id=edge.id, source_id=edge.source_node_id, target_id=edge.target_node_id,
            relation=edge.relation, confidence=confidence,
        ))

    class WorkspaceNeighbors:
        def outgoing_edges(self, requested_workspace_id: str, node_id: str):
            return outgoing.get(node_id, []) if requested_workspace_id == workspace_id else []

    hits = pruned_two_hop_retrieve(
        workspace_id, seeds, WorkspaceNeighbors(),
        config=PrunedTwoHopConfig(top_k=max(limit * 2, 8)),
    )
    reached = {hit.node_id for hit in hits}
    relevant_edges = [
        edge for edge in edges
        if edge.relation == "contradicts"
        and ({edge.source_node_id, edge.target_node_id} & reached)
    ]
    relevant_evidence = [
        evidence for hit in hits for evidence in retrievable[hit.node_id].evidence
    ] + [evidence for edge in relevant_edges for evidence in edge.evidence]
    if hasattr(store, "get_source_materials"):
        source_versions, source_spans = store.get_source_materials(
            workspace_id,
            [item.source_version_id for item in relevant_evidence][:400],
            [item.source_span_id for item in relevant_evidence][:400],
        )
        if hasattr(store, "load_scoped_graph_papers"):
            paper_pages: dict[str, set[int | None]] = {}
            for evidence in relevant_evidence[:400]:
                version = source_versions.get(evidence.source_version_id)
                span = source_spans.get(evidence.source_span_id)
                if version is not None and version.paper_id and span is not None:
                    paper_pages.setdefault(version.paper_id, set()).add(span.page)
            graph_papers = store.load_scoped_graph_papers(
                workspace_id, paper_pages, paper_ids=paper_ids,
                year_from=year_from, year_to=year_to, chunk_limit=200,
            )
            allowed_papers.update({paper.id: paper for paper in graph_papers})
    else:  # Narrow compatibility seam for custom/test store implementations.
        source_versions = source_spans = None
    node_candidates: list[tuple[str, Citation]] = []
    for hit in hits:
        node = retrievable.get(hit.node_id)
        if node is None:
            continue
        path = [{
            "edge_id": step.edge_id, "from_node_id": step.from_node_id,
            "to_node_id": step.to_node_id, "relation": step.relation,
            "confidence": step.confidence,
        } for step in hit.hop_path]
        path_is_negative = any(step.relation == "contradicts" for step in hit.hop_path)
        for evidence in node.evidence:
            stance = "negative" if path_is_negative or evidence.role == "contradicts" else "positive"
            citation = _paper_backed_graph_citation(
                store, workspace_id, allowed_papers, evidence=evidence,
                source_kind="graph_node", score=hit.score,
                knowledge_node_id=node.id, graph_path=path,
                retrieval_reason=hit.retrieval_reason, retrieval_stance=stance,
                source_versions=source_versions, source_spans=source_spans,
            )
            if citation is not None:
                node_candidates.append((f"node:{node.id}:{evidence.id}", citation))
                break

    contradiction_candidates: list[tuple[str, Citation]] = []
    hit_scores = {hit.node_id: hit.score for hit in hits}
    for edge in relevant_edges:
        try:
            confidence = float(edge.metadata.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        query_path_score = max(
            hit_scores.get(edge.source_node_id, 0.0),
            hit_scores.get(edge.target_node_id, 0.0),
        )
        combined_score = 0.65 * query_path_score + 0.35 * max(0.0, min(1.0, confidence))
        path = [{
            "edge_id": edge.id, "from_node_id": edge.source_node_id,
            "to_node_id": edge.target_node_id, "relation": edge.relation,
            "confidence": confidence,
        }]
        for evidence in edge.evidence:
            citation = _paper_backed_graph_citation(
                store, workspace_id, allowed_papers, evidence=evidence,
                source_kind="graph_edge", score=combined_score,
                knowledge_edge_id=edge.id, graph_path=path,
                retrieval_reason="query_graph_seed; graph_contradiction",
                retrieval_stance="negative",
                source_versions=source_versions, source_spans=source_spans,
            )
            if citation is not None:
                contradiction_candidates.append((f"edge:{edge.id}:{evidence.id}", citation))
                break
    return {
        "graph_nodes": node_candidates,
        "graph_contradicts": contradiction_candidates,
    }


def _safe_graph_citation_candidates(
    store: PaperStore, workspace_id: str, query: str, papers: list[Paper], limit: int,
    *, paper_ids: list[str] | None = None, year_from: int | None = None,
    year_to: int | None = None,
) -> dict[str, list[tuple[str, Citation]]]:
    """Keep the optional graph channel from taking down paper retrieval."""
    try:
        return _graph_citation_candidates(
            store, workspace_id, query, papers, limit, paper_ids=paper_ids,
            year_from=year_from, year_to=year_to,
        )
    except Exception as exc:
        # Do not include the query, graph content, or database details in logs.
        logger.warning(
            "code=graph_retrieval_unavailable exception_type=%s",
            exc.__class__.__name__,
        )
        return {"graph_nodes": [], "graph_contradicts": []}


def _fused_retrieval_citations(
    paper_citations: list[Citation], graph_candidates: dict[str, list[tuple[str, Citation]]],
    limit: int, *, require_contradiction: bool = False,
) -> list[Citation]:
    candidate_map = {item.chunk_id: item for item in paper_citations}
    rankings = {"paper": list(candidate_map)}
    for channel, candidates in graph_candidates.items():
        channel_keys: list[str] = []
        for _, citation in candidates:
            key = citation.chunk_id
            if key not in channel_keys:
                channel_keys.append(key)
            current = candidate_map.get(key)
            # Keep the most auditable representation of a shared chunk.  A
            # contradiction edge wins over a node, which wins over plain paper,
            # while the strongest relevance score is retained independently.
            priority = {"paper_chunk": 0, "graph_node": 1, "graph_edge": 2}
            if current is None or priority[citation.source_kind] > priority[current.source_kind]:
                candidate_map[key] = citation.model_copy(update={
                    "score": max(citation.score, current.score if current else 0.0),
                })
            elif citation.score > current.score:
                # Keep score and provenance from the same candidate.
                candidate_map[key] = citation
        rankings[channel] = channel_keys
    fused = reciprocal_rank_fusion(rankings, limit=max(len(candidate_map), 1))
    selected = sorted(
        fused, key=lambda item: (-item[1], -candidate_map[item[0]].score, item[0]),
    )[:max(limit, 1)]
    negative = graph_candidates.get("graph_contradicts", [])
    if require_contradiction and negative and not any(
        "graph_contradicts" in channels
        or any(step.get("relation") == "contradicts" for step in candidate_map[key].graph_path)
        for key, _, channels in selected
    ):
        replacement_key = negative[0][1].chunk_id
        replacement = (replacement_key, 1.0 / 61.0, ["graph_contradicts"])
        selected = ([*selected[:-1], replacement] if selected else [replacement])
    result: list[Citation] = []
    for index, (key, fusion_score, channels) in enumerate(selected, 1):
        result.append(candidate_map[key].model_copy(update={
            "index": index, "fusion_score": round(fusion_score, 6),
            "retrieval_channels": channels,
        }))
    return result


def _paper_summary_citations(paper: Paper, limit: int = 6) -> list:
    """Pick bounded, page-addressable evidence without invoking a search model."""
    ordered = sorted(paper.chunks, key=lambda chunk: (chunk.page, chunk.id))
    if len(ordered) > limit:
        # Keep the opening material plus evenly spaced later pages so a long
        # paper's summary is not exclusively about its introduction.
        positions = sorted({round(index * (len(ordered) - 1) / (limit - 1)) for index in range(limit)})
        ordered = [ordered[position] for position in positions]
    return citations_from([(paper, chunk, 1.0 - index * 0.01) for index, chunk in enumerate(ordered)], query=None)


def _local_paper_markdown_summary(paper: Paper, citations: list) -> str:
    if not citations:
        return "## 要約\n\n本文から要約に必要な抜粋を取得できませんでした。"
    bullets = "\n".join(f"- {citation.excerpt[:420]} [{citation.index}]" for citation in citations)
    return (
        f"## {paper.title} の要約\n\n"
        "### 原文から確認できる要点\n\n"
        f"{bullets}\n\n"
        "### 利用上の注意\n\n"
        "これはLLMを使わない抽出要約です。数式・数値・前提は各引用ページの原文で確認してください。"
    )


def _generate_paper_summary_with_llm(paper: Paper, citations: list, timeout_seconds: float) -> str:
    """Generate one bounded Markdown summary; the caller owns fallback policy."""
    from openai import OpenAI

    evidence = "\n\n".join(
        f"[{citation.index}] {citation.paper_title}, p.{citation.page}, {citation.section}\n{citation.excerpt}"
        for citation in citations
    )
    response = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        timeout=max(0.5, min(timeout_seconds, 20.0)),
        max_retries=0,
    ).responses.create(
        model=ANSWER_MODEL,
        store=False,
        instructions=(
            "あなたは研究論文を要約するアシスタントです。日本語Markdownで簡潔に書いてください。"
            "見出しは『要点』『手法・根拠』『限界・確認事項』を基本にし、根拠にある事実には直後に [1] の形式で引用を付けます。"
            "根拠にない事実・数値は追加しません。数式が根拠に含まれるときだけ、原表記を壊さずLaTeXの $...$ または $$...$$ で示します。"
        ),
        input=f"論文タイトル: {paper.title}\n\n根拠:\n{evidence}",
    )
    return response.output_text.strip()


@app.post("/api/papers/{paper_id}/summary", response_model=PaperMarkdownSummary)
def summarize_paper(
    paper_id: str,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    """Summarize one ready paper with bounded, page-linked evidence."""
    require_workspace_write(context)
    try:
        paper = store.get_owned(context.workspace.id, paper_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="論文が見つかりません") from exc
    if paper.status != "ready" or not paper.chunks:
        raise HTTPException(status_code=422, detail="解析完了した本文のある論文だけを要約できます")
    citations = _paper_summary_citations(paper)
    if not os.getenv("OPENAI_API_KEY"):
        return PaperMarkdownSummary(
            paper_id=paper.id, title=paper.title,
            summary=_local_paper_markdown_summary(paper, citations), citations=citations,
            generation_mode="local_fallback", model=None, fallback_reason="api_key_missing",
        )
    try:
        rendered = _generate_paper_summary_with_llm(paper, citations, PAPER_SUMMARY_DEADLINE_SECONDS)
        if not rendered:
            raise RuntimeError("empty summary response")
        _set_last_llm_failure(None)
        return PaperMarkdownSummary(
            paper_id=paper.id, title=paper.title, summary=rendered, citations=citations,
            generation_mode="llm", model=ANSWER_MODEL,
        )
    except Exception as exc:
        reason = _classify_llm_failure(exc)
        _set_last_llm_failure(reason)
        logger.warning("paper_summary_fallback code=%s exception=%s", reason, exc.__class__.__name__)
        return PaperMarkdownSummary(
            paper_id=paper.id, title=paper.title,
            summary=_local_paper_markdown_summary(paper, citations), citations=citations,
            generation_mode="local_fallback", model=None, fallback_reason=reason,
        )


@app.post("/api/search", response_model=SearchResponse)
def answer(
    body: SearchRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    # Full answers may invoke the embedding and chat providers and can persist a
    # conversation.  Keep this cost-bearing operation to writers.
    require_workspace_write(context)
    return _answer(body, store, context)


@app.post("/api/search/preview", response_model=SearchPreviewResponse)
def search_preview(
    body: SearchRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    """Return local lexical matches for every workspace member without side effects.

    This is deliberately a separate route from answer generation: it performs
    no embedding, LLM, persistence, or SSE work, so viewers can inspect source
    evidence without consuming the workspace's model budget.
    """
    papers = filtered_papers(body, store, context.workspace.id)
    return SearchPreviewResponse(citations=citations_from(search(papers, body.query, body.limit), query=body.query))


def _load_answer_conversation(
    body: SearchRequest, store: PaperStore, context: WorkspaceContext,
) -> ResearchConversation | None:
    if not body.conversation_id:
        return None
    try:
        conversation = store.get_conversation_metadata(
            context.workspace.id, body.conversation_id,
        )
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research conversation not found") from exc
    # Reject read-only users before any embedding or LLM API cost is incurred.
    require_workspace_write(context)
    return conversation


def _build_research_memory_context(
    store: PaperStore, workspace_id: str, conversation: ResearchConversation | None,
    query: str,
) -> str:
    if conversation is None:
        return ""
    durable = store.search_research_memory(
        workspace_id, conversation.id, query, limit=8,
    )
    durable_text = "\n".join(
        f"- {item.kind}: {item.content}" for item in durable
    )[:2_500]
    parts = [conversation.summary[-2_500:]] if conversation.summary else []
    if durable_text:
        parts.append("関連する長期研究メモリ:\n" + durable_text)
    return "\n\n".join(parts)[-5_000:]


def _run_agentic_rag(
    body: SearchRequest, retrieve, conversation: ResearchConversation | None,
    memory_context: str, deadline: float, emit: Callable[[str], None],
):
    agent = AgenticRAG(
        _build_agentic_chat_model(),
        retrieve,
        max_iterations=2,
        max_execution_seconds=RAG_REQUEST_DEADLINE_SECONDS,
        max_queries_per_iteration=3,
        max_evidence=max(body.limit, 10),
        model_factory=_build_agentic_chat_model,
        max_sources=6,
        max_evidence_chars=14_000,
        # Research memory is persisted only after the semantic audit.
        verify_clean_claims=conversation is not None,
        progress_callback=lambda stage: emit({
            "retrieving": "retrieving",
            "planning": "planning",
            "reranking": "planning",
            "generating": "generating",
            "verifying": "auditing",
            "completed": "saving",
            "fallback": "saving",
        }.get(stage, "retrieving")),
    )
    return agent.run(body.query, memory=memory_context, deadline=deadline)


def _search_result_summary(response: SearchResponse) -> dict:
    result = {
        "citations": [citation.model_dump(mode="json") for citation in response.citations],
        "generation_mode": response.generation_mode,
        "model": response.model,
        "retrieval_queries": response.retrieval_queries,
        "grounded": response.grounded,
        "llm_attempted": response.llm_attempted,
        "llm_succeeded": response.llm_succeeded,
        "grounding_status": response.grounding_status,
        "fallback_reason": response.fallback_reason,
        "claims": [claim.model_dump(mode="json") for claim in response.claims],
        "memory_delta": response.memory_delta,
        "model_calls": response.model_calls,
        "interaction_mode": response.interaction_mode,
        "draft": response.draft,
    }
    if os.getenv("SEARCH_HISTORY_STORE_ANSWER", "false").lower() in {"1", "true", "yes"}:
        result["answer"] = response.answer
    return result


def _persist_search_history_best_effort(
    store: PaperStore, context: WorkspaceContext, body: SearchRequest,
    response: SearchResponse, result_summary: dict,
) -> None:
    if context.workspace.role == "viewer":
        return
    try:
        store.add_search_history(
            context.workspace.id, context.user.id, body.query,
            body.paper_ids or list(dict.fromkeys(
                citation.paper_id for citation in response.citations
            )),
            result_summary,
        )
    except Exception:
        # History is auxiliary. Never turn a committed conversation answer into
        # a failed API/SSE response, and never log query, answer, or DB details.
        logger.warning("code=search_history_write_failed")


def _initial_conversation_title(query: str) -> str:
    """Produce a stable, readable title without an extra model request."""
    compact = " ".join(query.split())
    maximum = 64
    return compact if len(compact) <= maximum else compact[: maximum - 1].rstrip() + "…"


def _claim_classification(claim: dict) -> str:
    """Translate legacy claim kinds into the CI-005 audit vocabulary."""
    if claim.get("kind") == "paper" and claim.get("citation_ids"):
        return "evidence_backed"
    if claim.get("kind") == "general":
        return "general_knowledge"
    if claim.get("kind") == "hypothesis":
        return "hypothesis"
    return "inference" if claim.get("citation_ids") else "unverified"


def _research_message_response_metadata(response: SearchResponse) -> dict:
    """Persist only the typed, replay-relevant part of an assistant response."""
    metadata = {
        "interaction_mode": response.interaction_mode,
        "draft": response.draft,
        "claims": [claim.model_dump(mode="json") for claim in response.claims],
    }
    if response.research_run_id:
        metadata["research_run_id"] = response.research_run_id
    return metadata


def _mode_claims(body: SearchRequest, citations: list, claims: list[dict]) -> tuple[str, list[dict], bool]:
    """Apply the explicit research interaction contract without weakening citations.

    AgenticRAG remains responsible for grounded synthesis.  These bounded mode
    adapters make non-synthesis requests auditable even when the critic/model
    is unavailable and the request falls back to extractive evidence.
    """
    mode = body.interaction_mode
    normalized = [{**claim, "classification": _claim_classification(claim)} for claim in claims]
    if mode == "evidence":
        evidence_claims = normalized or [
            {"claim_id": f"evidence-{citation.index}", "text": citation.excerpt,
             "kind": "paper", "citation_ids": [citation.index], "classification": "evidence_backed"}
            for citation in citations[:6]
        ]
        return "", evidence_claims, False
    if mode == "explore":
        mechanisms = ["機構A: 表現・検索空間の整合性", "機構B: 評価信号による選択圧", "機構C: 条件依存の媒介要因"]
        mode_claims = [{"claim_id": f"explore-{index}", "text": text, "kind": "hypothesis", "citation_ids": [], "classification": "hypothesis"}
                       for index, text in enumerate(mechanisms, start=1)]
        return "\n\n## 異なる機構の探索案\n" + "\n".join(f"- {text}" for text in mechanisms), mode_claims, True
    if mode == "challenge":
        contradiction = next((
            item for item in citations
            if item.retrieval_stance == "negative"
            or "graph_contradicts" in item.retrieval_channels
            or any(step.get("relation") == "contradicts" for step in item.graph_path)
        ), None)
        items = ["競合仮説: 観測された効果は別の交絡要因で説明できる。"]
        mode_claims = [{
            "claim_id": "challenge-1", "text": items[0], "kind": "hypothesis",
            "citation_ids": [], "classification": "hypothesis",
        }]
        if contradiction is not None:
            text = f"取得済みの反証根拠: {contradiction.excerpt[:240]} [{contradiction.index}]"
            items.append(text)
            mode_claims.append({
                "claim_id": "challenge-2", "text": text, "kind": "paper",
                "citation_ids": [contradiction.index], "classification": "evidence_backed",
            })
        else:
            text = "反証根拠は取得できていません（unverified）。追加の反証検索と人間の確認が必要です。"
            items.append(text)
            mode_claims.append({
                "claim_id": "challenge-2", "text": text, "kind": "hypothesis",
                "citation_ids": [], "classification": "unverified",
            })
        return "\n\n## Challenge（批判的検討）\n" + "\n".join(f"- {text}" for text in items), mode_claims, True
    if mode == "design":
        text = "実験設計案: 競合仮説を区別する対照、測定指標、事前の判定基準を明示する。"
        return "\n\n## Design\n- " + text, [{"claim_id": "design-1", "text": text, "kind": "hypothesis", "citation_ids": [], "classification": "hypothesis"}], True
    if mode == "update":
        text = "更新候補: 新しい根拠によって支持・反証・保留のどれが変わるかを人間がレビューする。"
        return "\n\n## Update\n- " + text, [{"claim_id": "update-1", "text": text, "kind": "hypothesis", "citation_ids": [], "classification": "hypothesis"}], True
    return "", normalized, False


def _answer(
    body: SearchRequest,
    store: PaperStore,
    context: WorkspaceContext,
    *,
    progress: Callable[[str], None] | None = None,
    deadline: float | None = None,
) -> SearchResponse:
    """Answer within one absolute budget; retrieval never creates document vectors."""
    deadline = deadline or (time.monotonic() + RAG_REQUEST_DEADLINE_SECONDS)
    if body.interaction_mode == "evidence" and not body.paper_ids:
        raise HTTPException(status_code=422, detail="evidence mode requires selected paper_ids")

    def emit(stage: str) -> None:
        if progress is not None:
            progress(stage)

    conversation = _load_answer_conversation(body, store, context)

    vector_model = embedding_model()
    query_vector_cache: dict[str, list[float] | None] = {}
    graph_candidate_cache: dict[tuple[str, int], dict[str, list[tuple[str, Citation]]]] = {}
    candidate_cache: dict[tuple[str, int], tuple[list[Paper], dict[str, list[float]]]] = {}

    def load_candidates(scoped_query: str, limit: int) -> tuple[list[Paper], dict[str, list[float]]]:
        cache_key = (scoped_query, limit)
        cached = candidate_cache.get(cache_key)
        if cached is not None:
            return cached
        started = time.perf_counter_ns()
        papers = store.search_chunk_candidates(
            context.workspace.id, scoped_query, limit=limit,
            paper_ids=body.paper_ids, year_from=body.year_from, year_to=body.year_to,
        )
        chunks = [chunk for paper in papers for chunk in paper.chunks]
        logger.info(
            "rag_query_stage stage=db_candidates duration_ms=%.3f candidate_count=%d",
            (time.perf_counter_ns() - started) / 1_000_000, len(chunks),
        )
        emit("embedding")
        started = time.perf_counter_ns()
        embeddings = store.get_chunk_embeddings(
            context.workspace.id, [chunk.id for chunk in chunks], vector_model,
        )
        logger.info(
            "rag_query_stage stage=embedding_cache duration_ms=%.3f candidate_count=%d",
            (time.perf_counter_ns() - started) / 1_000_000, len(embeddings),
        )
        candidate_cache[cache_key] = (papers, embeddings)
        return papers, embeddings

    initial_papers, _ = load_candidates(body.query, body.limit)
    initial_chunks = [chunk for paper in initial_papers for chunk in paper.chunks]

    def retrieve(scoped_query: str, limit: int):
        emit("retrieving")
        papers, embeddings = load_candidates(scoped_query, limit)
        if scoped_query not in query_vector_cache:
            # Only the small query vector is generated on the request path. Paper
            # vectors are populated asynchronously by the embedding worker.
            remaining = deadline - time.monotonic()
            query_vectors = (
                embed_texts(
                    [scoped_query], timeout_seconds=min(2.5, max(0.5, remaining - 18.0)), max_retries=0,
                )
                if embeddings and remaining > 18.5 else []
            )
            query_vector_cache[scoped_query] = query_vectors[0] if query_vectors else None
        query_vector = query_vector_cache[scoped_query]
        started = time.perf_counter_ns()
        results = (
            hybrid_search(papers, scoped_query, embeddings, query_vector, limit)
            if query_vector is not None else search(papers, scoped_query, limit)
        )
        paper_citations = citations_from(results, query=scoped_query)
        graph_cache_key = (scoped_query, limit)
        if graph_cache_key not in graph_candidate_cache:
            graph_candidate_cache[graph_cache_key] = _safe_graph_citation_candidates(
                store, context.workspace.id, scoped_query, papers, limit,
                paper_ids=body.paper_ids, year_from=body.year_from, year_to=body.year_to,
            )
        citations = _fused_retrieval_citations(
            paper_citations, graph_candidate_cache[graph_cache_key], limit,
            require_contradiction=body.interaction_mode == "challenge",
        )
        logger.info(
            "rag_query_stage stage=rank_and_graph duration_ms=%.3f candidate_count=%d result_count=%d",
            (time.perf_counter_ns() - started) / 1_000_000,
            sum(len(paper.chunks) for paper in papers), len(citations),
        )
        return citations

    model_name = ANSWER_MODEL
    generated: str | None = None
    citations = []
    retrieval_queries = [body.query]
    grounded = False
    llm_attempted = False
    llm_succeeded = False
    grounding_status: Literal["verified", "rejected", "not_checked", "no_evidence"] = "not_checked"
    generation_mode = "local_fallback"
    fallback_reason: FallbackReason | None = None
    claims: list[dict] = []
    memory_delta: dict = {}
    model_calls = 0
    if not os.getenv("OPENAI_API_KEY"):
        fallback_reason = "api_key_missing"
        _set_last_llm_failure(fallback_reason)
    elif not initial_chunks and not any(
        graph_candidate_cache.setdefault(
            (body.query, body.limit), _safe_graph_citation_candidates(
                store, context.workspace.id, body.query, initial_papers, body.limit,
                paper_ids=body.paper_ids, year_from=body.year_from, year_to=body.year_to,
            ),
        ).values()
    ):
        fallback_reason = "no_evidence"
        grounding_status = "no_evidence"
        _set_last_llm_failure(fallback_reason)
    elif not _agentic_dependencies_available():
        fallback_reason = "dependency_missing"
        _set_last_llm_failure(fallback_reason)
    else:
        try:
            memory_context = _build_research_memory_context(
                store, context.workspace.id, conversation, body.query,
            )
            agent_result = _run_agentic_rag(
                body, retrieve, conversation, memory_context, deadline, emit,
            )
            generated = agent_result.answer
            citations = agent_result.citations
            retrieval_queries = agent_result.search_queries
            grounded = agent_result.grounded
            # Grounded results from older/custom AgenticRAG implementations necessarily
            # imply that an LLM was attempted and produced a usable answer.
            llm_attempted = agent_result.llm_attempted or agent_result.grounded
            llm_succeeded = agent_result.llm_succeeded or agent_result.grounded
            grounding_status = agent_result.grounding_status  # type: ignore[assignment]
            # An LLM generation that passed deterministic citation checks remains
            # an Agentic RAG answer even if the optional semantic audit timed out.
            generation_mode = "agentic_rag" if llm_succeeded else "local_fallback"
            fallback_reason = agent_result.fallback_reason  # type: ignore[assignment]
            claims = agent_result.claims
            memory_delta = agent_result.memory_delta
            model_calls = agent_result.model_calls
            _set_last_llm_failure(fallback_reason)
        except Exception as exc:
            llm_attempted = True
            fallback_reason = _classify_llm_failure(exc)
            _set_last_llm_failure(fallback_reason)
            logger.warning(
                "Agentic RAG fell back locally: code=%s exception=%s",
                fallback_reason, exc.__class__.__name__,
            )
            citations = retrieve(body.query, body.limit)
            generated = extractive_answer(body.query, citations)
    if generated is None:
        citations = retrieve(body.query, body.limit)
        generated = extractive_answer(body.query, citations)

    mode_appendix, classified_claims, mode_draft = _mode_claims(body, citations, claims)
    response = SearchResponse(
        answer=generated, citations=citations, conversation_id=body.conversation_id,
        research_run_id=body.research_run_id,
        interaction_mode=body.interaction_mode, draft=mode_draft or (body.interaction_mode == "synthesis" and not grounded),
        generation_mode=generation_mode, model=model_name if llm_succeeded else None,
        retrieval_queries=retrieval_queries, grounded=grounded,
        llm_attempted=llm_attempted, llm_succeeded=llm_succeeded,
        grounding_status=grounding_status, fallback_reason=fallback_reason,
        claims=classified_claims, memory_delta=memory_delta, model_calls=model_calls,
    )
    if mode_appendix:
        response.answer += mode_appendix
    # Write the completed response, including its mode appendix and replay
    # metadata, so a conversation reload is identical to the live answer.
    emit("saving")
    if conversation:
        store.record_research_exchange(
            context.workspace.id, conversation.id, body.query, response.answer, citations,
            memory_delta=memory_delta,
            response_metadata=_research_message_response_metadata(response),
        )
    result_summary = _search_result_summary(response)
    _persist_search_history_best_effort(store, context, body, response, result_summary)
    return response


@app.get("/api/research/conversations", response_model=list[ResearchConversation])
def list_research_conversations(
    store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context),
):
    return store.list_conversations(context.workspace.id)


@app.post("/api/research/conversations", response_model=ResearchConversation, status_code=201)
def create_research_conversation(
    body: ResearchConversationCreate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    # The Ask UI sends its first question as title.  Normalizing it here keeps
    # titles deterministic even for non-UI API clients, without an LLM call.
    return store.create_conversation(
        context.workspace.id, context.user.id, _initial_conversation_title(body.title),
    )


@app.get("/api/research/conversations/{conversation_id}", response_model=ResearchConversationDetail)
def get_research_conversation(
    conversation_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return store.get_conversation(context.workspace.id, conversation_id)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research conversation not found") from exc


@app.get(
    "/api/research/conversations/{conversation_id}/messages",
    response_model=ResearchMessagePage,
)
def get_research_messages_page(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    before_ordinal: int | None = Query(default=None, ge=1),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return store.list_research_messages_page(
            context.workspace.id, conversation_id,
            limit=limit, before_ordinal=before_ordinal,
        )
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research conversation not found") from exc


@app.get(
    "/api/research/conversations/{conversation_id}/memory",
    response_model=ResearchMemoryPage,
)
def get_research_memory_page(
    conversation_id: str,
    kind: Literal["hypothesis", "assumption", "unresolved_question", "planned_test"] | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    before_ordinal: int | None = Query(default=None, ge=1),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return store.list_research_memory_page(
            context.workspace.id, conversation_id,
            kind=kind, limit=limit, before_ordinal=before_ordinal,
        )
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research conversation not found") from exc


@app.post("/api/search/stream")
async def answer_stream(
    body: SearchRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    # Check before opening an SSE response so a viewer cannot hold a stream or
    # trigger embedding/LLM work before authorization is enforced.
    require_workspace_write(context)
    async def events():
        # Open the SSE response before retrieval/model work begins. Comment frames
        # are valid SSE and are deliberately ignored by existing clients.
        yield ": stream-open\n\n"
        yield f"data: {json.dumps({'type': 'stage', 'value': 'accepted'})}\n\n"
        run_id = body.research_run_id
        if run_id:
            try:
                store.start_research_run(context.workspace.id, run_id)
            except PaperNotFoundError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'research run not found'}, ensure_ascii=False)}\n\n"
                return
            except ValueError as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'run', 'run_id': run_id})}\n\n"
        stages: asyncio.Queue[str] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def report_progress(stage: str) -> None:
            loop.call_soon_threadsafe(stages.put_nowait, stage)

        deadline = time.monotonic() + RAG_REQUEST_DEADLINE_SECONDS
        task = asyncio.create_task(asyncio.to_thread(
            _answer, body, store, context, progress=report_progress, deadline=deadline,
        ))
        try:
            while not task.done():
                stage_task = asyncio.create_task(stages.get())
                done, _ = await asyncio.wait(
                    {task, stage_task}, timeout=10.0, return_when=asyncio.FIRST_COMPLETED,
                )
                if stage_task in done:
                    stage = stage_task.result()
                    yield f"data: {json.dumps({'type': 'stage', 'value': stage})}\n\n"
                    if run_id and store.research_run_cancel_requested(context.workspace.id, run_id):
                        task.cancel()
                        store.finish_research_run(context.workspace.id, run_id, status="cancelled")
                        yield f"data: {json.dumps({'type': 'cancelled', 'run_id': run_id})}\n\n"
                        return
                else:
                    stage_task.cancel()
                if task in done:
                    break
                if not done:
                    yield ": heartbeat\n\n"
            response = await task
        except asyncio.CancelledError:
            # Cancelling the await stops further streaming. Python cannot forcibly
            # terminate an already-running SDK call in the worker thread; its own
            # request timeout remains the final bound for that call.
            task.cancel()
            if run_id:
                try:
                    store.cancel_research_run(context.workspace.id, run_id)
                except (PaperNotFoundError, ValueError):
                    pass
            raise
        except HTTPException as exc:
            if run_id:
                store.append_run_artifact(context.workspace.id, run_id, kind="error", payload={"message": str(exc.detail)})
                store.finish_research_run(context.workspace.id, run_id, status="failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc.detail)}, ensure_ascii=False)}\n\n"
            return
        except Exception:
            logger.exception("Streaming RAG generation failed")
            if run_id:
                store.append_run_artifact(context.workspace.id, run_id, kind="error", payload={"message": "generation_failed"})
                store.finish_research_run(context.workspace.id, run_id, status="failed")
            yield f"data: {json.dumps({'type': 'error', 'message': '回答生成に失敗しました'}, ensure_ascii=False)}\n\n"
            return
        if run_id:
            if store.research_run_cancel_requested(context.workspace.id, run_id):
                store.finish_research_run(context.workspace.id, run_id, status="cancelled")
                yield f"data: {json.dumps({'type': 'cancelled', 'run_id': run_id})}\n\n"
                return
            response.research_run_id = run_id
            store.append_run_artifact(context.workspace.id, run_id, kind="retrieval_candidates", payload={
                "candidates": [citation.model_dump(mode="json") for citation in response.citations],
            })
            store.append_run_artifact(context.workspace.id, run_id, kind="validation", payload={
                "grounded": response.grounded, "grounding_status": response.grounding_status,
                "claims": [claim.model_dump(mode="json") for claim in response.claims],
            })
            store.finish_research_run(context.workspace.id, run_id, status="succeeded")
        words = re.split(r"(?<=。)|(?<=\n)", response.answer)
        for word in words:
            if word:
                yield f"data: {json.dumps({'type': 'token', 'value': word}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'citations', 'value': [c.model_dump() for c in response.citations]}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'meta', 'value': {'research_run_id': response.research_run_id, 'interaction_mode': response.interaction_mode, 'draft': response.draft, 'generation_mode': response.generation_mode, 'model': response.model, 'retrieval_queries': response.retrieval_queries, 'grounded': response.grounded, 'llm_attempted': response.llm_attempted, 'llm_succeeded': response.llm_succeeded, 'grounding_status': response.grounding_status, 'fallback_reason': response.fallback_reason, 'claims': [claim.model_dump(mode='json') for claim in response.claims], 'memory_delta': response.memory_delta, 'model_calls': response.model_calls}}, ensure_ascii=False)}\n\n"
        yield "data: {\"type\":\"done\"}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def selected_papers(body: AnalysisRequest, store: PaperStore, workspace_id: str) -> list[Paper]:
    papers = [p for p in store.list(workspace_id) if p.id in body.paper_ids]
    if not papers:
        raise HTTPException(status_code=404, detail="対象論文が見つかりません")
    return papers


@app.post("/api/analysis/compare", response_model=list[ComparisonRow])
def compare(
    body: AnalysisRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    return compare_papers(selected_papers(body, store, context.workspace.id))


@app.post("/api/analysis/gaps", response_model=list[ResearchGap])
def gaps(
    body: AnalysisRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    return research_gaps(selected_papers(body, store, context.workspace.id))


def _graph_not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail="graph resource not found")


@app.get("/api/graph", response_model=GraphSnapshot)
def graph_snapshot(
    canvas_id: str = Query(default="default", min_length=1, max_length=64),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    return GraphSnapshot(
        nodes=store.list_knowledge_nodes(context.workspace.id),
        edges=store.list_knowledge_edges(context.workspace.id),
        layouts=store.list_canvas_layouts(context.workspace.id, canvas_id),
    )


@app.get("/api/graph/sources", response_model=list[SourceVersion])
def list_graph_sources(
    kind: str | None = Query(default=None, max_length=32),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    return store.list_source_versions(context.workspace.id, kind)


@app.post("/api/graph/sources", response_model=SourceVersion, status_code=201)
def create_graph_source(
    body: SourceVersionCreate, store: PaperStore = Depends(get_store),
    originals: OriginalStorage = Depends(get_original_storage),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    content_hash = body.content_hash.lower()
    metadata = dict(body.metadata)
    if body.paper_id is not None:
        raise HTTPException(status_code=422, detail="paper sources are created only by the ingestion pipeline")
    if body.kind.strip().casefold() == "paper":
        raise HTTPException(status_code=422, detail="paper source kind is reserved for the ingestion pipeline")
    if body.content is None:
        raise HTTPException(status_code=422, detail="source content is required")
    if body.content is not None:
        calculated_hash = hashlib.sha256(body.content.encode("utf-8")).hexdigest()
        if calculated_hash != content_hash:
            raise HTTPException(status_code=422, detail="content_hash does not match source content")
        # Source Version content is immutable, but unlike a paper original it is
        # created by the API after ingestion.  Keep it in the API-writable asset
        # root so a production deployment can mount paper originals read-only.
        storage_key = f"assets/sources/{content_hash}/source.txt"
        try:
            originals.put(storage_key, body.content.encode("utf-8"))
        except ImmutableObjectExists:
            pass
        except Exception as exc:
            raise HTTPException(status_code=500, detail="source content could not be persisted") from exc
        metadata["storage_key"] = storage_key
    try:
        return store.create_source_version(
            context.workspace.id, kind=body.kind, locator=body.locator,
            content_hash=content_hash, paper_id=body.paper_id, metadata=metadata,
        )
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/graph/sources/import", response_model=SourceImportResult, status_code=201)
def import_graph_source(
    body: SourceImportCreate, store: PaperStore = Depends(get_store),
    originals: OriginalStorage = Depends(get_original_storage),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    """Store a content-verified source and derive bounded, typed provenance spans."""
    require_workspace_write(context)
    encoded = body.content.encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    if content_hash != body.content_hash.lower():
        raise HTTPException(status_code=422, detail="content_hash does not match source content")
    try:
        parsed = parse_source(body.kind, encoded)
    except SourceParseLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(parsed.spans) > 10_000:
        raise HTTPException(status_code=413, detail="parsed source has too many spans")
    # See create_graph_source: imported Source Versions must not depend on the
    # read-mostly original-paper mount being writable.
    storage_key = f"assets/sources/{content_hash}/source.txt"
    created_asset = False
    try:
        originals.put(storage_key, encoded)
        created_asset = True
    except ImmutableObjectExists:
        pass
    except Exception as exc:
        raise HTTPException(status_code=500, detail="source content could not be persisted") from exc

    metadata = dict(body.metadata)
    metadata.update(dict(parsed.metadata))
    metadata["storage_key"] = storage_key
    try:
        source, spans = store.create_source_import(
            context.workspace.id, kind=parsed.source_kind, locator=body.locator,
            content_hash=content_hash, metadata=metadata,
            spans=[{
                "page": item.page, "line_start": item.line_start, "line_end": item.line_end,
                "char_start": item.char_start, "char_end": item.char_end,
                "cell": item.cell_index,
                "locator": {"kind": item.kind, "anchor": item.locator, **dict(item.metadata)},
                "text": item.text,
            } for item in parsed.spans],
        )
        return SourceImportResult(source=source, spans=spans)
    except PaperNotFoundError as exc:
        if created_asset:
            try:
                originals.delete(storage_key)
            except Exception:
                logger.exception("Could not compensate failed source import: key=%s", storage_key)
        raise _graph_not_found(exc) from exc
    except ResourceConflictError as exc:
        if created_asset:
            try:
                originals.delete(storage_key)
            except Exception:
                logger.exception("Could not compensate failed source import: key=%s", storage_key)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        if created_asset:
            try:
                originals.delete(storage_key)
            except Exception:
                logger.exception("Could not compensate failed source import: key=%s", storage_key)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        if created_asset:
            try:
                originals.delete(storage_key)
            except Exception:
                logger.exception("Could not compensate failed source import: key=%s", storage_key)
        raise HTTPException(status_code=500, detail="source metadata could not be persisted") from exc


@app.get("/api/graph/sources/{source_version_id}/spans", response_model=list[SourceSpan])
def list_graph_source_spans(
    source_version_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return store.list_source_spans(context.workspace.id, source_version_id)
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc


@app.get("/api/hypotheses", response_model=list[HypothesisCard])
def list_hypothesis_cards(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_hypothesis_cards(context.workspace.id)

@app.get("/api/hypotheses/{card_id}", response_model=HypothesisCard)
def get_hypothesis_card(card_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    try: return store.get_hypothesis_card(context.workspace.id, card_id)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="hypothesis card not found") from exc


@app.get("/api/discovery/review-queue", response_model=list[DiscoveryItem])
def list_discovery_review_queue(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_discovery_items(context.workspace.id)


@app.get("/api/beliefs", response_model=list[BeliefEvent])
def search_beliefs(query: str = "", store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.search_positive_beliefs(context.workspace.id, query)


@app.post("/api/beliefs", response_model=BeliefEvent, status_code=201)
def append_belief_event(body: BeliefEventCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    return store.append_belief_event(context.workspace.id, context.user.id, body)

@app.post("/api/experiments", response_model=ExperimentPlan, status_code=201)
def create_experiment(body: ExperimentPlanCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.create_experiment_plan(context.workspace.id, context.user.id, body)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="hypothesis card not found") from exc

@app.get("/api/experiments", response_model=list[ExperimentPlan])
def list_experiments(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_experiment_plans(context.workspace.id)

@app.get("/api/experiments/{plan_id}", response_model=ExperimentPlan)
def get_experiment(plan_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    try: return store.get_experiment_plan(context.workspace.id, plan_id)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="experiment plan not found") from exc

@app.post("/api/experiments/{plan_id}/results", response_model=ExperimentPlan)
def record_experiment_result(plan_id: str, body: ExperimentResultCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.add_experiment_result(context.workspace.id, plan_id, body.model_dump())
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="experiment plan not found") from exc

@app.get("/api/experiments/{plan_id}/snapshot", response_model=ExperimentPlanSnapshot)
def export_experiment_snapshot(plan_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    try: return store.export_experiment_plan_snapshot(context.workspace.id, plan_id)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="experiment plan not found") from exc


@app.post("/api/discovery/items", response_model=DiscoveryItem, status_code=201)
def create_discovery_item(body: DiscoveryItemCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    return store.create_discovery_item(context.workspace.id, context.user.id, body)


@app.patch("/api/discovery/items/{item_id}/review", response_model=DiscoveryItem)
def review_discovery_item(item_id: str, body: DiscoveryReviewUpdate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.review_discovery_item(context.workspace.id, item_id, body.review_status)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="discovery item not found") from exc


@app.post("/api/hypotheses", response_model=HypothesisCard, status_code=201)
def create_hypothesis_card(body: HypothesisCardCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    return store.create_hypothesis_card(context.workspace.id, context.user.id, body)


@app.patch("/api/hypotheses/{card_id}/status", response_model=HypothesisCard)
def update_hypothesis_card_status(card_id: str, body: HypothesisCardStatusUpdate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try:
        return store.set_hypothesis_card_status(context.workspace.id, card_id, body.status, human_reviewed=body.human_reviewed, empirically_supported=body.empirically_supported)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="hypothesis card not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/graph/nodes", response_model=list[KnowledgeNode])
def list_graph_nodes(
    status: str | None = Query(default=None, max_length=32), layer: int | None = Query(default=None, ge=0),
    store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context),
):
    return store.list_knowledge_nodes(context.workspace.id, status=status, layer=layer)


@app.post("/api/graph/nodes", response_model=KnowledgeNode, status_code=201)
def create_graph_node(
    body: KnowledgeNodeCreate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.create_knowledge_node(
            context.workspace.id, node_type=body.node_type, content=body.content,
            layer=body.layer, status=body.status, phase=body.phase,
            confidence=body.confidence, created_by=context.user.id, metadata=body.metadata,
            evidence_span_ids=body.evidence_span_ids, evidence_excerpt=body.evidence_excerpt,
            evidence_links=body.evidence_links,
        )
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/graph/nodes/{node_id}", response_model=KnowledgeNode)
def get_graph_node(
    node_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return store.get_knowledge_node(context.workspace.id, node_id)
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc


@app.patch("/api/graph/nodes/{node_id}/status", response_model=KnowledgeNodeStatusResult)
def update_graph_node_status(
    node_id: str, body: KnowledgeNodeStatusUpdate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        node, affected = store.set_knowledge_node_status(context.workspace.id, node_id, body.status)
        return KnowledgeNodeStatusResult(node=node, affected_node_ids=affected)
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/graph/edges", response_model=KnowledgeEdge, status_code=201)
def create_graph_edge(
    body: KnowledgeEdgeCreate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.create_knowledge_edge(
            context.workspace.id, source_node_id=body.source_node_id,
            target_node_id=body.target_node_id, relation=body.relation,
            evidence_span_ids=body.evidence_span_ids, metadata=body.metadata,
            evidence_excerpt=body.evidence_excerpt, created_by=context.user.id,
            evidence_links=body.evidence_links,
        )
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/graph/edges/{edge_id}/status", response_model=KnowledgeEdge)
def update_graph_edge_status(
    edge_id: str, body: KnowledgeEdgeStatusUpdate,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.set_knowledge_edge_status(
            context.workspace.id, edge_id, status=body.status,
            actor_id=context.user.id, reason=body.reason,
        )
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/graph/runs", response_model=ReasoningRun, status_code=201)
def create_graph_reasoning_run(
    body: ReasoningRunCreate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.create_reasoning_run(
            context.workspace.id, operator=body.operator, input_node_ids=body.input_node_ids,
            output_node_ids=body.output_node_ids, prompt=body.prompt,
            created_by=context.user.id, metadata=body.metadata,
        )
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/graph/forward-propagations", response_model=ForwardPropagationResult, status_code=201)
def forward_propagate_graph_hypothesis(
    body: ForwardPropagationCreate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    """Create a pending hypothesis with atomic evidence and reasoning lineage."""
    require_workspace_write(context)
    try:
        hypothesis_content = body.hypothesis_content
        metadata = dict(body.metadata)
        if not hypothesis_content:
            inputs = [
                store.get_knowledge_node(context.workspace.id, node_id)
                for node_id in dict.fromkeys(body.input_node_ids)
            ]
            spans = [
                store.get_source_span(context.workspace.id, span_id)
                for span_id in dict.fromkeys([
                    *body.evidence_span_ids,
                    *(link.source_span_id for link in body.evidence_links),
                ])
            ]
            hypothesis_content, generation_metadata = _generate_forward_hypothesis(
                [node.content for node in inputs], [span.text for span in spans], body.prompt,
            )
            metadata.update(generation_metadata)
        else:
            metadata.setdefault("generation_mode", "provided")
        return store.forward_propagate_hypothesis(
            context.workspace.id, input_node_ids=body.input_node_ids,
            hypothesis_content=hypothesis_content,
            evidence_span_ids=body.evidence_span_ids,
            evidence_excerpt=body.evidence_excerpt, prompt=body.prompt,
            operator=body.operator, metadata=metadata,
            confidence=body.confidence, phase=body.phase,
            created_by=context.user.id, evidence_links=body.evidence_links,
        )
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/graph/nodes/{node_id}/feedback", response_model=NodeFeedback)
def add_graph_node_feedback(
    node_id: str, body: NodeFeedbackCreate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.upsert_node_feedback(
            context.workspace.id, node_id, context.user.id, verdict=body.verdict,
            rating=body.rating, comment=body.comment,
        )
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/graph/nodes/{node_id}/layout", response_model=CanvasLayout)
def update_graph_layout(
    node_id: str, body: CanvasLayoutUpdate, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_write(context)
    try:
        return store.upsert_canvas_layout(
            context.workspace.id, node_id, x=body.x, y=body.y, canvas_id=body.canvas_id,
            width=body.width, height=body.height, z_index=body.z_index, collapsed=body.collapsed,
        )
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/graph/retrieve", response_model=list[GraphRetrievalHit])
def retrieve_graph(
    body: GraphRetrieveRequest, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    retrievable_statuses = {"active", "verified"}
    retrievable_node_ids = {
        node.id for node in store.list_knowledge_nodes(context.workspace.id)
        if node.status in retrievable_statuses
    }
    graph_edges = [
        edge for edge in store.list_knowledge_edges(context.workspace.id)
        if edge.status in {"active", "verified"}
        and edge.source_node_id in retrievable_node_ids and edge.target_node_id in retrievable_node_ids
    ]
    outgoing: dict[str, list[RetrievalGraphEdge]] = {}
    for edge in graph_edges:
        raw_confidence = edge.metadata.get("confidence", 1.0)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 1.0
        outgoing.setdefault(edge.source_node_id, []).append(RetrievalGraphEdge(
            id=edge.id, source_id=edge.source_node_id, target_id=edge.target_node_id,
            relation=edge.relation, confidence=confidence,
        ))

    class AuthorizedNeighbors:
        def outgoing_edges(self, workspace_id: str, node_id: str):
            if workspace_id != context.workspace.id:
                return []
            return outgoing.get(node_id, [])

    hits = pruned_two_hop_retrieve(
        context.workspace.id,
        [RetrievalSeed(
            node_id=seed.node_id, relevance=seed.relevance, confidence=seed.confidence,
            retrieval_reason=seed.retrieval_reason,
        ) for seed in body.seeds if seed.node_id in retrievable_node_ids],
        AuthorizedNeighbors(),
        config=PrunedTwoHopConfig(
            top_k=body.top_k, max_degree=body.max_degree,
            max_first_hop_candidates=body.max_first_hop_candidates,
        ),
    )
    result: list[GraphRetrievalHit] = []
    for hit in hits:
        try:
            node = store.get_knowledge_node(context.workspace.id, hit.node_id)
        except PaperNotFoundError:
            continue
        if node.status not in retrievable_statuses:
            continue
        result.append(GraphRetrievalHit(
            node=node, score=hit.score, retrieval_reason=hit.retrieval_reason,
            hop_count=hit.hop_count,
            hop_path=[{
                "edge_id": step.edge_id, "from_node_id": step.from_node_id,
                "to_node_id": step.to_node_id, "relation": step.relation,
                "confidence": step.confidence,
            } for step in hit.hop_path],
        ))
    return result


@app.get("/api/tags", response_model=list[Tag])
def list_tags(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_tags(context.workspace.id)


@app.post("/api/tags", response_model=Tag, status_code=201)
def create_tag(body: TagCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.create_tag(context.workspace.id, body.name, body.color)
    except ResourceConflictError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.put("/api/tags/{tag_id}", response_model=Tag)
def update_tag(tag_id: str, body: TagCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.update_tag(context.workspace.id, tag_id, body.name, body.color)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="tag not found") from exc
    except ResourceConflictError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    if not store.delete_tag(context.workspace.id, tag_id): raise HTTPException(status_code=404, detail="tag not found")


@app.get("/api/papers/{paper_id}/tags", response_model=list[Tag])
def paper_tags(paper_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    try: return store.get_paper_tags(context.workspace.id, paper_id)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="paper not found") from exc


@app.put("/api/papers/{paper_id}/tags", response_model=list[Tag])
def set_paper_tags(paper_id: str, body: PaperTagsUpdate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.set_paper_tags(context.workspace.id, paper_id, body.tag_ids)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="paper or tag not found") from exc


@app.get("/api/notes", response_model=list[Note])
def list_notes(paper_id: str | None = None, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_notes(context.workspace.id, paper_id)


@app.post("/api/notes", response_model=Note, status_code=201)
def create_note(body: NoteCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.create_note(context.workspace.id, context.user.id, body.paper_id, body.title, body.content)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="paper not found") from exc


@app.patch("/api/notes/{note_id}", response_model=Note)
def update_note(note_id: str, body: NoteUpdate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    try: return store.update_note(context.workspace.id, note_id, body.title, body.content)
    except PaperNotFoundError as exc: raise HTTPException(status_code=404, detail="note not found") from exc


@app.delete("/api/notes/{note_id}", status_code=204)
def delete_note(note_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    if not store.delete_note(context.workspace.id, note_id): raise HTTPException(status_code=404, detail="note not found")


@app.get("/api/search/history", response_model=list[SearchHistory])
def search_history(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_search_history(context.workspace.id)


@app.delete("/api/search/history/{history_id}", status_code=204)
def delete_history(history_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    if not store.delete_search_history(context.workspace.id, history_id): raise HTTPException(status_code=404, detail="history not found")


@app.get("/api/comparisons", response_model=list[SavedComparison])
def list_saved_comparisons(store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    return store.list_comparisons(context.workspace.id)


@app.post("/api/comparisons", response_model=SavedComparison, status_code=201)
def save_comparison(body: SavedComparisonCreate, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    papers = selected_papers(AnalysisRequest(paper_ids=body.paper_ids), store, context.workspace.id)
    if len(papers) != len(set(body.paper_ids)): raise HTTPException(status_code=404, detail="paper not found")
    result = [row.model_dump(mode="json") for row in compare_papers(papers)]
    snapshot = body.citation_snapshot or [evidence for row in result for evidence in row.get("evidence", [])]
    try:
        return store.save_comparison(context.workspace.id, context.user.id, body.name, body.paper_ids, result, source_set_id=body.source_set_id, citation_snapshot=snapshot, human_judgment=body.human_judgment, judgment_reason=body.judgment_reason)
    except PaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail="source set not found") from exc


@app.delete("/api/comparisons/{comparison_id}", status_code=204)
def delete_comparison(comparison_id: str, store: PaperStore = Depends(get_store), context: WorkspaceContext = Depends(get_workspace_context)):
    require_workspace_write(context)
    if not store.delete_comparison(context.workspace.id, comparison_id): raise HTTPException(status_code=404, detail="comparison not found")


def _csv_safe(value: object) -> str:
    text_value = "" if value is None else str(value)
    return "'" + text_value if re.match(r"^\s*[=+\-@]", text_value) else text_value


def _flat_export_value(value: object) -> str:
    return re.sub(r"[\r\n]+", " ", "" if value is None else str(value)).strip()


def _bibtex_value(value: object) -> str:
    return _flat_export_value(value).replace("\\", "\\textbackslash{} ").replace("{", "\\{").replace("}", "\\}")


@app.get("/api/exports/papers")
def export_papers(
    format: str = Query(pattern="^(bibtex|ris|csv)$"),
    paper_ids: list[str] = Query(default=[]),
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    papers = store.list(context.workspace.id)
    if paper_ids:
        selected = [paper for paper in papers if paper.id in set(paper_ids)]
        if len(selected) != len(set(paper_ids)): raise HTTPException(status_code=404, detail="paper not found")
        papers = selected
    if format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["id", "title", "authors", "year", "source", "external_id"])
        for p in papers: writer.writerow([_csv_safe(p.id), _csv_safe(p.title), _csv_safe("; ".join(p.authors)), _csv_safe(p.year), _csv_safe(p.source), _csv_safe(p.external_id)])
        content, media, ext = output.getvalue(), "text/csv; charset=utf-8", "csv"
    elif format == "ris":
        blocks = []
        for p in papers:
            lines = ["TY  - JOUR", f"TI  - {_flat_export_value(p.title)}"] + [f"AU  - {_flat_export_value(a)}" for a in p.authors]
            if p.year: lines.append(f"PY  - {p.year}")
            if p.external_id: lines.append(f"DO  - {_flat_export_value(p.external_id)}")
            lines.append("ER  - "); blocks.append("\r\n".join(lines))
        content, media, ext = "\r\n\r\n".join(blocks), "application/x-research-info-systems; charset=utf-8", "ris"
    else:
        entries = []
        for p in papers:
            key = re.sub(r"[^A-Za-z0-9_-]", "", (p.authors[0].split()[-1] if p.authors else "paper") + str(p.year or "nd") + p.id[:6])
            fields = [f"  title = {{{_bibtex_value(p.title)}}}", f"  author = {{{_bibtex_value(' and '.join(p.authors))}}}"]
            if p.year: fields.append(f"  year = {{{p.year}}}")
            if p.external_id: fields.append(f"  doi = {{{_bibtex_value(p.external_id)}}}")
            entries.append(f"@article{{{key},\n" + ",\n".join(fields) + "\n}")
        content, media, ext = "\n\n".join(entries), "application/x-bibtex; charset=utf-8", "bib"
    return Response(content=content.encode("utf-8-sig") if format == "csv" else content.encode("utf-8"), media_type=media, headers={"Content-Disposition": f'attachment; filename="paperpilot-export.{ext}"'})
