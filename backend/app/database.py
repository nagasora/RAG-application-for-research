from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_personal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    personal_owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceMemberRecord(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'editor', 'viewer')", name="ck_workspace_members_role"),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchQuestionRecord(Base):
    """A durable research question within, not above, a workspace."""

    __tablename__ = "research_questions"
    __table_args__ = (Index("ix_research_questions_workspace_updated", "workspace_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceSetRecord(Base):
    """A named, workspace-scoped selection of papers."""

    __tablename__ = "source_sets"
    __table_args__ = (Index("ix_source_sets_workspace_updated", "workspace_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceSetPaperRecord(Base):
    __tablename__ = "source_set_papers"
    __table_args__ = (
        UniqueConstraint("source_set_id", "paper_id", name="uq_source_set_papers_set_paper"),
        Index("ix_source_set_papers_paper", "paper_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_set_id: Mapped[str] = mapped_column(ForeignKey("source_sets.id", ondelete="CASCADE"), nullable=False, index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class ResearchRunRecord(Base):
    """Immutable execution context, distinct from a chat conversation or graph reasoning run."""

    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_research_runs_status"),
        Index("ix_research_runs_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    research_question_id: Mapped[str | None] = mapped_column(ForeignKey("research_questions.id", ondelete="SET NULL"), index=True)
    source_set_id: Mapped[str | None] = mapped_column(ForeignKey("source_sets.id", ondelete="SET NULL"), index=True)
    research_question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_paper_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    excluded_paper_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    success_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="")
    plan: Mapped[dict | list] = mapped_column(JSON, nullable=False, default=dict)
    model: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdeaRecord(Base):
    __tablename__ = "ideas"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('observation', 'interpretation', 'hypothesis', 'falsifier', 'todo')",
            name="ck_ideas_kind",
        ),
        CheckConstraint("status IN ('unverified', 'promoted')", name="ck_ideas_status"),
        Index("ix_ideas_workspace_status_created", "workspace_id", "status", "created_at"),
        Index("ix_ideas_research_run", "research_run_id"),
        Index("ix_ideas_paper", "paper_id"),
        Index("ix_ideas_source_span", "source_span_id"),
        Index("ix_ideas_hypothesis_card", "hypothesis_card_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="hypothesis")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    research_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"))
    claim_id: Mapped[str | None] = mapped_column(String(128))
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    source_span_id: Mapped[str | None] = mapped_column(ForeignKey("source_spans.id", ondelete="SET NULL"))
    checklist: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unverified")
    hypothesis_card_id: Mapped[str | None] = mapped_column(ForeignKey("hypothesis_cards.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewThreadRecord(Base):
    __tablename__ = "review_threads"
    __table_args__ = (
        CheckConstraint(
            "(evidence_ref_id IS NOT NULL AND research_run_id IS NULL AND claim_id IS NULL "
            "AND claim_artifact_id IS NULL AND claim_snapshot IS NULL) OR "
            "(evidence_ref_id IS NULL AND research_run_id IS NOT NULL AND claim_id IS NOT NULL "
            "AND claim_artifact_id IS NOT NULL AND claim_snapshot IS NOT NULL)",
            name="ck_review_threads_one_anchor",
        ),
        CheckConstraint("status IN ('open', 'resolved')", name="ck_review_threads_status"),
        Index("ix_review_threads_workspace_status_updated", "workspace_id", "status", "updated_at"),
        Index("ix_review_threads_assigned", "assigned_to", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    research_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id", ondelete="RESTRICT"), index=True)
    claim_id: Mapped[str | None] = mapped_column(String(128))
    claim_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("run_artifacts.id", ondelete="RESTRICT"), index=True)
    claim_snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    evidence_ref_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_refs.id", ondelete="RESTRICT"), index=True)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewCommentRecord(Base):
    __tablename__ = "review_comments"
    __table_args__ = (Index("ix_review_comments_thread_created", "review_thread_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_thread_id: Mapped[str] = mapped_column(ForeignKey("review_threads.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewDecisionRecord(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('accepted', 'rejected', 'changes_requested', 'needs_more_evidence')",
            name="ck_review_decisions_verdict",
        ),
        Index("ix_review_decisions_thread_created", "review_thread_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_thread_id: Mapped[str] = mapped_column(ForeignKey("review_threads.id", ondelete="CASCADE"), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunArtifactRecord(Base):
    __tablename__ = "run_artifacts"
    __table_args__ = (
        UniqueConstraint("research_run_id", "ordinal", name="uq_run_artifacts_run_ordinal"),
        Index("ix_run_artifacts_run_created", "research_run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict | list] = mapped_column(JSON, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperRecord(Base):
    __tablename__ = "papers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "content_hash", name="uq_papers_workspace_content_hash"),
        UniqueConstraint("storage_key", name="uq_papers_storage_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Retained only for rolling compatibility with the stage-3 schema/API.
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    external_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    chunks: Mapped[list["ChunkRecord"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", passive_deletes=True
    )


class ChunkRecord(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False, default="本文")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    paper: Mapped[PaperRecord] = relationship(back_populates="chunks")


class ChunkEmbeddingRecord(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TagRecord(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_tags_workspace_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperTagRecord(Base):
    __tablename__ = "paper_tags"
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class PaperDecisionRecord(Base):
    __tablename__ = "paper_decisions"
    __table_args__ = (
        CheckConstraint("decision IN ('undecided', 'included', 'excluded')", name="ck_paper_decisions_decision"),
        Index("ix_paper_decisions_workspace_decision", "workspace_id", "decision"),
    )
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="undecided")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NoteRecord(Base):
    __tablename__ = "notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SearchHistoryRecord(Base):
    __tablename__ = "search_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    paper_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    result_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchConversationRecord(Base):
    __tablename__ = "research_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memory_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchMessageRecord(Base):
    __tablename__ = "research_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_research_messages_role"),
        UniqueConstraint("conversation_id", "ordinal", name="uq_research_messages_conversation_ordinal"),
        Index("ix_research_messages_conversation_ordinal", "conversation_id", "ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("research_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchMemoryEventRecord(Base):
    """Immutable, source-linked facts extracted from a research conversation."""

    __tablename__ = "research_memory_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('hypothesis', 'assumption', 'unresolved_question', 'planned_test')",
            name="ck_research_memory_events_kind",
        ),
        UniqueConstraint(
            "conversation_id", "ordinal", name="uq_research_memory_events_conversation_ordinal"
        ),
        UniqueConstraint(
            "conversation_id", "kind", "content_hash",
            name="uq_research_memory_events_conversation_kind_hash",
        ),
        Index(
            "ix_research_memory_events_workspace_conversation_ordinal",
            "workspace_id", "conversation_id", "ordinal",
        ),
        Index(
            "ix_research_memory_events_conversation_kind_ordinal",
            "conversation_id", "kind", "ordinal",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("research_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_messages.id", ondelete="SET NULL"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SavedComparisonRecord(Base):
    __tablename__ = "saved_comparisons"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    paper_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    result: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    source_set_id: Mapped[str | None] = mapped_column(ForeignKey("source_sets.id", ondelete="SET NULL"), index=True)
    citation_snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    human_judgment: Mapped[str] = mapped_column(String(16), nullable=False, default="unreviewed")
    judgment_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestionJobRecord(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmbeddingJobRecord(Base):
    __tablename__ = "embedding_jobs"
    __table_args__ = (
        UniqueConstraint("paper_id", "provider", "model", name="uq_embedding_jobs_paper_provider_model"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_embedding_jobs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperPageRecord(Base):
    __tablename__ = "paper_pages"
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_source: Mapped[str] = mapped_column(String(16), nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)


class DocumentElementRecord(Base):
    __tablename__ = "document_elements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    bbox: Mapped[list[float] | None] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured_data: Mapped[dict | list | None] = mapped_column(JSON)
    asset_key: Mapped[str | None] = mapped_column(Text, unique=True)


# The research graph deliberately does not reuse ``papers``/``chunks`` as its
# primary identity.  A paper can be re-imported, while a source version and its
# spans must remain immutable so every generated node can be audited later.
class SourceVersionRecord(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "locator", "content_hash", name="uq_source_versions_workspace_kind_locator_content_hash"),
        Index("ix_source_versions_workspace_kind_created", "workspace_id", "kind", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    locator: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceSpanRecord(Base):
    __tablename__ = "source_spans"
    __table_args__ = (
        Index("ix_source_spans_source_version_page", "source_version_id", "page"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    page: Mapped[int | None] = mapped_column(Integer)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSON)
    cell: Mapped[dict | list | None] = mapped_column(JSON)
    locator_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HypothesisCardRecord(Base):
    """Structured, workspace-scoped hypothesis; review evidence is never implied."""
    __tablename__ = "hypothesis_cards"
    __table_args__ = (Index("ix_hypothesis_cards_workspace_updated", "workspace_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    mechanism: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conditions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    intervention: Mapped[str] = mapped_column(Text, nullable=False, default="")
    outcome: Mapped[str] = mapped_column(Text, nullable=False, default="")
    direction: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    assumptions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    competing_theories: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    predictions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    falsifiers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    test: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    human_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    empirically_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DiscoveryItemRecord(Base):
    __tablename__ = "discovery_items"
    __table_args__ = (Index("ix_discovery_items_workspace_status_created", "workspace_id", "review_status", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_paper_id: Mapped[str] = mapped_column(String(256), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_quote: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    license: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    rate_limit_policy: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BeliefEventRecord(Base):
    __tablename__ = "belief_events"
    __table_args__ = (Index("ix_belief_events_workspace_belief_created", "workspace_id", "belief_key", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    belief_key: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hypothesis_card_id: Mapped[str | None] = mapped_column(String(36))
    reasoning_run_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExperimentPlanRecord(Base):
    __tablename__ = "experiment_plans"
    __table_args__ = (
        Index("ix_experiment_plans_workspace_updated", "workspace_id", "updated_at"),
        Index("ix_experiment_plans_hypothesis_card", "hypothesis_card_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    hypothesis_card_id: Mapped[str | None] = mapped_column(
        ForeignKey("hypothesis_cards.id", ondelete="SET NULL")
    )
    plan: Mapped[dict] = mapped_column(JSON, nullable=False)
    results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeNodeRecord(Base):
    __tablename__ = "knowledge_nodes"
    __table_args__ = (
        CheckConstraint("node_type IN ('source', 'idea', 'constraint', 'hypothesis', 'experiment')", name="ck_knowledge_nodes_type"),
        CheckConstraint("status IN ('review_pending', 'active', 'verified', 'rejected', 'superseded', 'review_required', 'pruned')", name="ck_knowledge_nodes_status"),
        CheckConstraint("layer >= 0", name="ck_knowledge_nodes_layer"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_knowledge_nodes_confidence"),
        Index("ix_knowledge_nodes_workspace_layer_status", "workspace_id", "layer", "status"),
        Index("ix_knowledge_nodes_workspace_phase", "workspace_id", "phase"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="review_pending")
    layer: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(String(64), nullable=False, default="unclassified")
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeEdgeRecord(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        CheckConstraint("source_node_id != target_node_id", name="ck_knowledge_edges_distinct_nodes"),
        CheckConstraint("relation IN ('informs', 'supports', 'extends', 'formulates', 'contradicts', 'implements', 'depends_on', 'related')", name="ck_knowledge_edges_relation"),
        CheckConstraint("status IN ('review_pending', 'active', 'verified', 'rejected', 'superseded', 'review_required', 'pruned')", name="ck_knowledge_edges_status"),
        CheckConstraint("origin IN ('manual', 'llm', 'import')", name="ck_knowledge_edges_origin"),
        Index("ix_knowledge_edges_workspace_source", "workspace_id", "source_node_id"),
        Index("ix_knowledge_edges_workspace_target", "workspace_id", "target_node_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    target_node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeEdgeStatusEventRecord(Base):
    __tablename__ = "knowledge_edge_status_events"
    __table_args__ = (Index("ix_edge_status_events_edge_created", "knowledge_edge_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_edge_id: Mapped[str] = mapped_column(ForeignKey("knowledge_edges.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvidenceRefRecord(Base):
    __tablename__ = "evidence_refs"
    __table_args__ = (
        CheckConstraint(
            "(knowledge_node_id IS NOT NULL AND knowledge_edge_id IS NULL) OR "
            "(knowledge_node_id IS NULL AND knowledge_edge_id IS NOT NULL)",
            name="ck_evidence_refs_one_subject",
        ),
        CheckConstraint("role IN ('supports', 'contradicts', 'context', 'mentions')", name="ck_evidence_refs_role"),
        CheckConstraint("extraction_quality IN ('high', 'medium', 'low', 'unknown')", name="ck_evidence_refs_extraction_quality"),
        CheckConstraint("quote_start >= 0 AND quote_end >= quote_start", name="ck_evidence_refs_quote_offsets"),
        Index("ix_evidence_refs_node", "knowledge_node_id"),
        Index("ix_evidence_refs_edge", "knowledge_edge_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    source_span_id: Mapped[str] = mapped_column(ForeignKey("source_spans.id", ondelete="RESTRICT"), nullable=False, index=True)
    knowledge_node_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"))
    knowledge_edge_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_edges.id", ondelete="CASCADE"))
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    target_claim: Mapped[str] = mapped_column(Text, nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="supports")
    extraction_quality: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    quote_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quote_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verbatim_quote: Mapped[str] = mapped_column(Text, nullable=False, default="")
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReasoningRunRecord(Base):
    __tablename__ = "reasoning_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_reasoning_runs_status"),
        Index("ix_reasoning_runs_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    operator: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReasoningRunInputRecord(Base):
    __tablename__ = "reasoning_run_inputs"
    __table_args__ = (UniqueConstraint("reasoning_run_id", "knowledge_node_id", name="uq_reasoning_run_inputs_run_node"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reasoning_run_id: Mapped[str] = mapped_column(ForeignKey("reasoning_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="RESTRICT"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class ReasoningRunOutputRecord(Base):
    __tablename__ = "reasoning_run_outputs"
    __table_args__ = (UniqueConstraint("reasoning_run_id", "knowledge_node_id", name="uq_reasoning_run_outputs_run_node"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reasoning_run_id: Mapped[str] = mapped_column(ForeignKey("reasoning_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="RESTRICT"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class NodeFeedbackRecord(Base):
    __tablename__ = "node_feedback"
    __table_args__ = (
        CheckConstraint("verdict IN ('helpful', 'not_helpful', 'accepted', 'rejected')", name="ck_node_feedback_verdict"),
        CheckConstraint("rating IS NULL OR (rating >= -1 AND rating <= 1)", name="ck_node_feedback_rating"),
        UniqueConstraint("knowledge_node_id", "user_id", name="uq_node_feedback_node_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    rating: Mapped[float | None] = mapped_column(Float)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CanvasLayoutRecord(Base):
    __tablename__ = "canvas_layouts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "canvas_id", "knowledge_node_id", name="uq_canvas_layouts_canvas_node"),
        Index("ix_canvas_layouts_workspace_canvas", "workspace_id", "canvas_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    canvas_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    knowledge_node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    z_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collapsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def create_database_engine(database_url: str) -> Engine:
    if not database_url:
        raise ValueError("DATABASE_URL is required; no implicit database fallback is configured")
    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
