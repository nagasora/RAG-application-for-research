from __future__ import annotations

import io
import csv
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pypdf import PdfReader

from .auth import get_current_principal
from .models import (
    AnalysisRequest, Chunk, ComparisonRow, ExternalPaperRequest, MeResponse, Paper, PaperDetail,
    DocumentElement, IngestionJob, Note, NoteCreate, NoteUpdate, PaperPage, PaperSummary, PaperTagsUpdate, Principal,
    ResearchGap, SavedComparison, SavedComparisonCreate, SearchHistory, SearchRequest,
    SearchResponse, Tag, TagCreate, UploadResult, User, Workspace, WorkspaceCreate,
)
from .rag import chunk_pages, citations_from, compare_papers, extractive_answer, llm_answer, research_gaps, search
from .ingestion import process_ingestion_job
from .storage import LocalOriginalStorage, OriginalStorage
from .store import DuplicatePaperError, PaperNotFoundError, PaperStore, ResourceConflictError, WorkspaceAccessError

load_dotenv()


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


MAX_UPLOAD_FILES = _positive_int_env("MAX_UPLOAD_FILES", 10)
MAX_UPLOAD_BYTES = _positive_int_env("MAX_UPLOAD_BYTES", 25 * 1024 * 1024)
MAX_PDF_PAGES = _positive_int_env("MAX_PDF_PAGES", 300)
READ_CHUNK_BYTES = 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=4)
def _store_for_url(database_url: str) -> PaperStore:
    return PaperStore(database_url)


def get_store() -> PaperStore:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    return _store_for_url(database_url)


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
        store.upsert(paper)
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
    return FileResponse(path, media_type=paper.mime_type, filename=f"{paper.title}{extension}")


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
    deleted = store.delete(context.workspace.id, paper_id)
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


@app.post("/api/search", response_model=SearchResponse)
def answer(
    body: SearchRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    results = search(filtered_papers(body, store, context.workspace.id), body.query, body.limit)
    citations = citations_from(results)
    generated = llm_answer(body.query, citations) or extractive_answer(body.query, citations)
    response = SearchResponse(answer=generated, citations=citations)
    result_summary = {"citations": [citation.model_dump(mode="json") for citation in citations]}
    if os.getenv("SEARCH_HISTORY_STORE_ANSWER", "false").lower() in {"1", "true", "yes"}:
        result_summary["answer"] = generated
    store.add_search_history(
        context.workspace.id, context.user.id, body.query,
        body.paper_ids or list(dict.fromkeys(c.paper_id for c in citations)), result_summary,
    )
    return response


@app.post("/api/search/stream")
def answer_stream(
    body: SearchRequest,
    store: PaperStore = Depends(get_store),
    context: WorkspaceContext = Depends(get_workspace_context),
):
    response = answer(body, store, context)

    def events():
        words = re.split(r"(?<=。)|(?<=\n)", response.answer)
        for word in words:
            if word:
                yield f"data: {json.dumps({'type': 'token', 'value': word}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'citations', 'value': [c.model_dump() for c in response.citations]}, ensure_ascii=False)}\n\n"
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
