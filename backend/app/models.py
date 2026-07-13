from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("workspace name is required")
        return cleaned


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
    query: str = Field(min_length=2, max_length=4000)
    paper_ids: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    limit: int = Field(default=8, ge=1, le=20)
    conversation_id: str | None = None


class Citation(BaseModel):
    index: int
    paper_id: str
    paper_title: str
    chunk_id: str
    page: int
    section: str
    excerpt: str
    score: float


class AnswerClaim(BaseModel):
    claim_id: str
    text: str
    kind: Literal["paper", "general", "hypothesis"]
    citation_ids: list[int] = Field(default_factory=list)


class SearchResponse(BaseModel):
    answer: str
    citations: list[Citation]
    conversation_id: str | None = None
    generation_mode: Literal["agentic_rag", "local_fallback"] = "local_fallback"
    model: str | None = None
    retrieval_queries: list[str] = Field(default_factory=list)
    grounded: bool = False
    llm_attempted: bool = False
    llm_succeeded: bool = False
    grounding_status: Literal["verified", "rejected", "not_checked", "no_evidence"] = "not_checked"
    claims: list[AnswerClaim] = Field(default_factory=list)
    memory_delta: dict = Field(default_factory=dict)
    model_calls: int = Field(default=0, ge=0)
    fallback_reason: Literal[
        "api_key_missing", "dependency_missing", "no_evidence", "grounding_failed",
        "authentication_failed", "permission_denied", "model_not_found", "rate_limited",
        "api_timeout", "network_error", "model_api_error", "deadline_exceeded",
        "model_timeout", "model_unavailable", "provider_unavailable", "model_call_failed",
        "generation_failed", "citation_validation_failed", "grounding_audit_failed", "repair_failed",
        "structured_output_invalid", "verification_skipped_timeout",
    ] | None = None


class LLMStatus(BaseModel):
    configured: bool
    model: str
    embedding_model: str
    agentic_dependencies_available: bool
    last_failure_code: Literal[
        "api_key_missing", "dependency_missing", "no_evidence", "grounding_failed",
        "authentication_failed", "permission_denied", "model_not_found", "rate_limited",
        "api_timeout", "network_error", "model_api_error", "deadline_exceeded",
        "model_timeout", "model_unavailable", "provider_unavailable", "model_call_failed",
        "generation_failed", "citation_validation_failed", "grounding_audit_failed", "repair_failed",
        "structured_output_invalid", "verification_skipped_timeout",
    ] | None = None


class ResearchConversationCreate(BaseModel):
    title: str = Field(default="新しい研究対話", min_length=1, max_length=255)


class ResearchConversation(BaseModel):
    id: str
    title: str
    summary: str
    message_count: int = 0
    memory_event_count: int = 0
    created_by: str
    created_at: str
    updated_at: str


class ResearchMessage(BaseModel):
    id: str
    conversation_id: str
    ordinal: int
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: str


class ResearchConversationDetail(ResearchConversation):
    messages: list[ResearchMessage] = Field(default_factory=list)


class ResearchMessagePage(BaseModel):
    items: list[ResearchMessage] = Field(default_factory=list)
    next_before_ordinal: int | None = None


class ResearchMemoryEvent(BaseModel):
    id: str
    conversation_id: str
    source_message_id: str | None = None
    ordinal: int
    kind: Literal["hypothesis", "assumption", "unresolved_question", "planned_test"]
    content: str
    created_at: str


class ResearchMemoryPage(BaseModel):
    items: list[ResearchMemoryEvent] = Field(default_factory=list)
    next_before_ordinal: int | None = None


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
