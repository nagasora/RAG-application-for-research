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
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pypdf import PdfReader

from .auth import get_current_principal
from .agentic_rag import AgenticRAG
from .models import (
    AnalysisRequest, Chunk, ComparisonRow, ExternalPaperRequest, LLMStatus, MeResponse, Paper, PaperDetail, PaperMarkdownSummary,
    DocumentElement, IngestionJob, Note, NoteCreate, NoteUpdate, PaperPage, PaperSummary, PaperTagsUpdate, Principal,
    ResearchGap, SavedComparison, SavedComparisonCreate, SearchHistory, SearchRequest,
    SearchResponse, Tag, TagCreate, UploadResult, User, Workspace, WorkspaceCreate,
    ResearchConversation, ResearchConversationCreate, ResearchConversationDetail,
    ResearchMemoryPage, ResearchMessagePage,
    WorkspaceMember, WorkspaceMemberCreate, WorkspaceMemberUpdate,
    CanvasLayout, CanvasLayoutUpdate, GraphRetrieveRequest, GraphRetrievalHit,
    GraphSnapshot, KnowledgeEdge, KnowledgeEdgeCreate, KnowledgeEdgeStatusUpdate, KnowledgeNode,
    KnowledgeNodeCreate, KnowledgeNodeStatusResult, KnowledgeNodeStatusUpdate, NodeFeedback,
    NodeFeedbackCreate, ReasoningRun, ReasoningRunCreate, SourceSpan,
    SourceImportCreate, SourceImportResult, SourceVersion, SourceVersionCreate,
)
from .graph_rag import GraphEdge as RetrievalGraphEdge, PrunedTwoHopConfig, RetrievalSeed, pruned_two_hop_retrieve
from .source_parsers import SourceParseLimitError, parse_source
from .rag import (
    ANSWER_MODEL, chunk_pages, citations_from, compare_papers, embed_texts, embedding_config, embedding_model, extractive_answer,
    hybrid_search, research_gaps, search,
)
from .ingestion import process_ingestion_job
from .storage import ImmutableObjectExists, LocalOriginalStorage, OriginalStorage
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
    legacy = os.getenv("PAPER_STORAGE_DIR")
    original = Path(os.getenv("PAPER_ORIGINAL_STORAGE_DIR", legacy or "./data/originals"))
    assets = Path(os.getenv("PAPER_ASSET_STORAGE_DIR", "./data/assets" if not legacy else legacy))
    original_root = original if original.is_absolute() else ROOT / original
    asset_root = assets if assets.is_absolute() else ROOT / assets
    return _storage_for_roots(str(original_root.resolve()), str(asset_root.resolve()))


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
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
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


@app.post("/api/papers/upload", response_model=list[UploadResult])
async def upload_papers(
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
            failed = store.mark_failed(paper.id, message)
            results.append(UploadResult(filename=filename, success=False, status="failed", paper=summary(failed), error=message))
            continue
        if os.getenv("INGESTION_MODE", "inline").lower() == "celery":
            from .celery_app import enqueue_ingestion
            try:
                enqueue_ingestion(paper.id, job.id)
                results.append(UploadResult(filename=filename, success=True, status="processing", paper=summary(paper), job=job))
            except Exception as exc:
                message = f"ingestion queue unavailable: {exc}"
                store.abort_queued_ingestion(job.id, paper.id, message)
                results.append(UploadResult(filename=filename, success=False, status="failed", paper=summary(store.get(paper.id)), error=message, job=store.get_ingestion_job(context.workspace.id, job.id)))
            continue
        try:
            process_ingestion_job(store, originals, job.id, paper.id)
            ready = store.get(paper.id)
            results.append(UploadResult(filename=filename, success=True, status="ready", paper=summary(ready), job=store.get_ingestion_job(context.workspace.id, job.id)))
        except Exception as exc:
            failed = store.get(paper.id)
            results.append(UploadResult(filename=filename, success=False, status="failed", paper=summary(failed), error=str(exc) or exc.__class__.__name__, job=store.get_ingestion_job(context.workspace.id, job.id)))
    if os.getenv("INGESTION_MODE", "inline").lower() == "celery":
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
        extracted_assets = [element.asset_key for element in store.list_document_elements(context.workspace.id, paper_id) if element.asset_key]
    except PaperNotFoundError:
        extracted_assets = []
    try:
        deleted = store.delete(context.workspace.id, paper_id)
    except ResourceConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="この論文は知識グラフの根拠として使用されているため削除できません",
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="論文が見つかりません")
    if deleted.storage_key:
        originals.delete(deleted.storage_key)
    for asset_key in extracted_assets:
        originals.delete(asset_key)


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
    return _answer(body, store, context)


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

    def emit(stage: str) -> None:
        if progress is not None:
            progress(stage)

    conversation = _load_answer_conversation(body, store, context)

    papers = filtered_papers(body, store, context.workspace.id)
    chunks = [chunk for paper in papers for chunk in paper.chunks]
    vector_model = embedding_model()
    emit("embedding")
    embeddings = store.get_chunk_embeddings(context.workspace.id, [chunk.id for chunk in chunks], vector_model)
    query_vector_cache: dict[str, list[float] | None] = {}

    def retrieve(scoped_query: str, limit: int):
        emit("retrieving")
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
        results = (
            hybrid_search(papers, scoped_query, embeddings, query_vector, limit)
            if query_vector is not None else search(papers, scoped_query, limit)
        )
        return citations_from(results, query=scoped_query)

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
    elif not chunks:
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

    emit("saving")
    if conversation:
        store.record_research_exchange(
            context.workspace.id, conversation.id, body.query, generated, citations,
            memory_delta=memory_delta,
        )
    response = SearchResponse(
        answer=generated, citations=citations, conversation_id=body.conversation_id,
        generation_mode=generation_mode, model=model_name if llm_succeeded else None,
        retrieval_queries=retrieval_queries, grounded=grounded,
        llm_attempted=llm_attempted, llm_succeeded=llm_succeeded,
        grounding_status=grounding_status, fallback_reason=fallback_reason,
        claims=claims, memory_delta=memory_delta, model_calls=model_calls,
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
    async def events():
        # Open the SSE response before retrieval/model work begins. Comment frames
        # are valid SSE and are deliberately ignored by existing clients.
        yield ": stream-open\n\n"
        yield f"data: {json.dumps({'type': 'stage', 'value': 'accepted'})}\n\n"
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
            raise
        except HTTPException as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc.detail)}, ensure_ascii=False)}\n\n"
            return
        except Exception:
            logger.exception("Streaming RAG generation failed")
            yield f"data: {json.dumps({'type': 'error', 'message': '回答生成に失敗しました'}, ensure_ascii=False)}\n\n"
            return
        words = re.split(r"(?<=。)|(?<=\n)", response.answer)
        for word in words:
            if word:
                yield f"data: {json.dumps({'type': 'token', 'value': word}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'citations', 'value': [c.model_dump() for c in response.citations]}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'meta', 'value': {'generation_mode': response.generation_mode, 'model': response.model, 'retrieval_queries': response.retrieval_queries, 'grounded': response.grounded, 'llm_attempted': response.llm_attempted, 'llm_succeeded': response.llm_succeeded, 'grounding_status': response.grounding_status, 'fallback_reason': response.fallback_reason, 'claims': [claim.model_dump(mode='json') for claim in response.claims], 'memory_delta': response.memory_delta, 'model_calls': response.model_calls}}, ensure_ascii=False)}\n\n"
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
    try:
        originals.put(storage_key, encoded)
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
        raise _graph_not_found(exc) from exc
    except ResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/graph/sources/{source_version_id}/spans", response_model=list[SourceSpan])
def list_graph_source_spans(
    source_version_id: str, store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    try:
        return store.list_source_spans(context.workspace.id, source_version_id)
    except PaperNotFoundError as exc:
        raise _graph_not_found(exc) from exc


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
    return store.save_comparison(context.workspace.id, context.user.id, body.name, body.paper_ids, result)


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
