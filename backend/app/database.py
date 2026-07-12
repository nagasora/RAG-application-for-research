from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine
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


class SavedComparisonRecord(Base):
    __tablename__ = "saved_comparisons"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    paper_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    result: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
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


def create_database_engine(database_url: str) -> Engine:
    if not database_url:
        raise ValueError("DATABASE_URL is required; no implicit database fallback is configured")
    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
