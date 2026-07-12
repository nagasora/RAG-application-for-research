from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Chunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    paper_id: str
    page: int
    section: str = "本文"
    text: str


class Paper(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    workspace_id: str
    created_by: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    source: str = "upload"
    external_id: str | None = None
    status: Literal["ready", "processing", "failed"] = "ready"
    page_count: int = 0
    created_at: str = Field(default_factory=utc_now)
    chunks: list[Chunk] = Field(default_factory=list)
    content_hash: str | None = None
    error_message: str | None = None
    storage_key: str | None = None
    mime_type: str | None = None
    byte_size: int | None = None


class PaperSummary(BaseModel):
    id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    source: str
    external_id: str | None
    status: str
    page_count: int
    chunk_count: int
    created_at: str
    error_message: str | None = None


class PaperDetail(PaperSummary):
    storage_key: str | None = None
    mime_type: str | None = None
    byte_size: int | None = None


class PaperPage(BaseModel):
    paper_id: str
    page: int
    chunks: list[Chunk]
    text: str = ""
    text_source: Literal["native", "ocr", "none"] = "none"
    quality: float = 0.0
    elements: list["DocumentElement"] = Field(default_factory=list)


class DocumentElement(BaseModel):
    id: str
    paper_id: str
    page: int
    kind: Literal["text", "table", "figure", "caption"]
    bbox: list[float] | None = None
    text: str = ""
    structured_data: dict | list | None = None
    asset_key: str | None = None


class IngestionJob(BaseModel):
    id: str
    paper_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int
    attempts: int
    error_message: str | None = None
    created_at: str
    updated_at: str


class UploadResult(BaseModel):
    filename: str
    success: bool
    status: Literal["processing", "ready", "failed", "duplicate", "rejected"]
    paper: PaperSummary | None = None
    error: str | None = None
    duplicate: bool = False
    job: "IngestionJob | None" = None


class Principal(BaseModel):
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None


class User(BaseModel):
    id: str
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    created_at: str


class Workspace(BaseModel):
    id: str
    name: str
    role: Literal["owner", "editor", "viewer"]
    is_personal: bool
    created_at: str


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class MeResponse(BaseModel):
    user: User
    personal_workspace: Workspace


class ExternalPaperRequest(BaseModel):
    # Kept for request compatibility only; authorization always uses the authenticated principal.
    user_id: str | None = None
    identifier: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""


class SearchRequest(BaseModel):
    # Kept for request compatibility only; authorization always uses the authenticated principal.
    user_id: str | None = None
    query: str = Field(min_length=2)
    paper_ids: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    limit: int = Field(default=8, ge=1, le=20)


class Citation(BaseModel):
    index: int
    paper_id: str
    paper_title: str
    chunk_id: str
    page: int
    section: str
    excerpt: str
    score: float


class SearchResponse(BaseModel):
    answer: str
    citations: list[Citation]


class AnalysisRequest(BaseModel):
    # Kept for request compatibility only; authorization always uses the authenticated principal.
    user_id: str | None = None
    paper_ids: list[str] = Field(min_length=1)


class ComparisonRow(BaseModel):
    paper_id: str
    title: str
    purpose: str
    method: str
    results: str
    limitations: str


class ResearchGap(BaseModel):
    paper_id: str
    paper_title: str
    page: str
    gap: str
    opportunity: str


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default="#64748b", min_length=1, max_length=32)


class Tag(TagCreate):
    id: str
    created_at: str


class PaperTagsUpdate(BaseModel):
    tag_ids: list[str] = Field(default_factory=list)


class NoteCreate(BaseModel):
    paper_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=100_000)


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=100_000)


class Note(BaseModel):
    id: str
    paper_id: str | None
    author_id: str
    title: str
    content: str
    created_at: str
    updated_at: str


class SearchHistory(BaseModel):
    id: str
    user_id: str
    query: str
    paper_ids: list[str]
    result_summary: dict
    created_at: str


class SavedComparisonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    paper_ids: list[str] = Field(min_length=1)


class SavedComparison(BaseModel):
    id: str
    user_id: str
    name: str
    paper_ids: list[str]
    result: list[dict]
    created_at: str
