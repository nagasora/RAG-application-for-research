from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
from uuid import uuid4

from sqlalchemy import case, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .database import (
    Base, CanvasLayoutRecord, ChunkEmbeddingRecord, ChunkRecord, DocumentElementRecord,
    EmbeddingJobRecord, EvidenceRefRecord, IngestionJobRecord, KnowledgeEdgeRecord,
    KnowledgeNodeRecord, NodeFeedbackRecord, NoteRecord, PaperPageRecord, PaperRecord,
    PaperTagRecord, ReasoningRunInputRecord, ReasoningRunOutputRecord, ReasoningRunRecord,
    ResearchConversationRecord, ResearchMemoryEventRecord, ResearchMessageRecord,
    SavedComparisonRecord, SearchHistoryRecord, SourceSpanRecord, SourceVersionRecord,
    TagRecord, UserRecord, WorkspaceMemberRecord, WorkspaceRecord,
    create_database_engine, create_session_factory,
)
from .models import (
    CanvasLayout, Chunk, Citation, DocumentElement, EvidenceRef, IngestionJob,
    KnowledgeEdge, KnowledgeNode, NodeFeedback, Note, Paper, PaperPage, Principal,
    ReasoningRun, ReasoningRunLink, ResearchConversation, ResearchConversationDetail,
    ResearchMemoryEvent, ResearchMemoryPage, ResearchMessage, ResearchMessagePage,
    SavedComparison, SearchHistory, SourceSpan, SourceVersion, Tag, User, Workspace,
)


class DuplicatePaperError(Exception):
    def __init__(self, paper: Paper):
        super().__init__(f"duplicate paper: {paper.id}")
        self.paper = paper


class PaperNotFoundError(Exception):
    pass


class WorkspaceAccessError(Exception):
    pass


class WorkspacePermissionError(Exception):
    pass


class ResourceConflictError(Exception):
    pass


@dataclass(frozen=True)
class EmbeddingJob:
    id: str
    workspace_id: str
    paper_id: str
    provider: str
    model: str
    status: str
    progress: int
    attempts: int
    total_chunks: int
    completed_chunks: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime


def _embedding_job_model(record: EmbeddingJobRecord) -> EmbeddingJob:
    return EmbeddingJob(
        id=record.id, workspace_id=record.workspace_id, paper_id=record.paper_id,
        provider=record.provider,
        model=record.model, status=record.status, progress=record.progress,
        attempts=record.attempts, total_chunks=record.total_chunks,
        completed_chunks=record.completed_chunks, error_code=record.error_code,
        created_at=record.created_at, updated_at=record.updated_at,
    )


def _queue_embedding_job(
    session: Session, *, paper_id: str, workspace_id: str,
    provider: str, model: str, total_chunks: int,
) -> None:
    if total_chunks < 1:
        return
    now = datetime.now(timezone.utc)
    record = session.scalar(
        select(EmbeddingJobRecord).where(
            EmbeddingJobRecord.paper_id == paper_id,
            EmbeddingJobRecord.provider == provider,
            EmbeddingJobRecord.model == model,
        ).with_for_update()
    )
    if record is None:
        session.add(EmbeddingJobRecord(
            id=str(uuid4()), workspace_id=workspace_id, paper_id=paper_id,
            provider=provider,
            model=model, status="queued", progress=0, attempts=0,
            total_chunks=total_chunks, completed_chunks=0, error_code=None,
            created_at=now, updated_at=now,
        ))
        return
    record.status = "queued"
    record.progress = 0
    record.attempts = 0
    record.total_chunks = total_chunks
    record.completed_chunks = 0
    record.error_code = None
    record.updated_at = now


def _job_model(record: IngestionJobRecord) -> IngestionJob:
    return IngestionJob(id=record.id, paper_id=record.paper_id, status=record.status, progress=record.progress, attempts=record.attempts, error_message=record.error_message, created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat())


def _element_model(record: DocumentElementRecord) -> DocumentElement:
    return DocumentElement(id=record.id, paper_id=record.paper_id, page=record.page, kind=record.kind, bbox=record.bbox, text=record.text, structured_data=record.structured_data, asset_key=record.asset_key)


def _parse_created_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_model(record: PaperRecord) -> Paper:
    return Paper(
        id=record.id,
        user_id=record.user_id,
        workspace_id=record.workspace_id,
        created_by=record.created_by,
        title=record.title,
        authors=list(record.authors or []),
        year=record.year,
        abstract=record.abstract,
        source=record.source,
        external_id=record.external_id,
        status=record.status,
        page_count=record.page_count,
        created_at=record.created_at.isoformat(),
        chunks=[
            Chunk(id=chunk.id, paper_id=chunk.paper_id, page=chunk.page, section=chunk.section, text=chunk.text)
            for chunk in sorted(record.chunks, key=lambda item: (item.page, item.id))
        ],
        content_hash=record.content_hash,
        error_message=record.error_message,
        storage_key=record.storage_key,
        mime_type=record.mime_type,
        byte_size=record.byte_size,
    )


def _record_from_model(paper: Paper) -> PaperRecord:
    if not paper.content_hash:
        raise ValueError("content_hash is required for persisted papers")
    return PaperRecord(
        id=paper.id,
        workspace_id=paper.workspace_id,
        created_by=paper.created_by,
        user_id=paper.user_id,
        title=paper.title,
        authors=list(paper.authors),
        year=paper.year,
        abstract=paper.abstract,
        source=paper.source,
        external_id=paper.external_id,
        status=paper.status,
        page_count=paper.page_count,
        created_at=_parse_created_at(paper.created_at),
        content_hash=paper.content_hash,
        error_message=paper.error_message,
        storage_key=paper.storage_key,
        mime_type=paper.mime_type,
        byte_size=paper.byte_size,
        chunks=[
            ChunkRecord(id=chunk.id, paper_id=paper.id, page=chunk.page, section=chunk.section, text=chunk.text)
            for chunk in paper.chunks
        ],
    )


def _user_model(record: UserRecord) -> User:
    return User(
        id=record.id,
        issuer=record.issuer,
        subject=record.subject,
        email=record.email,
        display_name=record.display_name,
        created_at=record.created_at.isoformat(),
    )


def _workspace_model(record: WorkspaceRecord, role: str) -> Workspace:
    return Workspace(
        id=record.id,
        name=record.name,
        role=role,
        is_personal=record.is_personal,
        created_at=record.created_at.isoformat(),
    )


def _source_version_model(record: SourceVersionRecord) -> SourceVersion:
    return SourceVersion(
        id=record.id, workspace_id=record.workspace_id, paper_id=record.paper_id,
        kind=record.kind, locator=record.locator, content_hash=record.content_hash,
        metadata=dict(record.metadata_json or {}), created_at=record.created_at.isoformat(),
    )


def _source_span_model(record: SourceSpanRecord) -> SourceSpan:
    return SourceSpan(
        id=record.id, workspace_id=record.workspace_id,
        source_version_id=record.source_version_id, page=record.page,
        line_start=record.line_start, line_end=record.line_end,
        char_start=record.char_start, char_end=record.char_end, bbox=record.bbox,
        cell=record.cell, locator=dict(record.locator_json or {}), text=record.text,
        created_at=record.created_at.isoformat(),
    )


def _evidence_ref_model(record: EvidenceRefRecord) -> EvidenceRef:
    return EvidenceRef(
        id=record.id, workspace_id=record.workspace_id,
        source_span_id=record.source_span_id,
        knowledge_node_id=record.knowledge_node_id,
        knowledge_edge_id=record.knowledge_edge_id, excerpt=record.excerpt,
        created_at=record.created_at.isoformat(),
    )


def _knowledge_node_model(
    record: KnowledgeNodeRecord, evidence: list[EvidenceRefRecord] | None = None,
) -> KnowledgeNode:
    return KnowledgeNode(
        id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
        node_type=record.node_type, status=record.status, layer=record.layer,
        content=record.content, phase=record.phase, confidence=record.confidence,
        metadata=dict(record.metadata_json or {}),
        evidence=[_evidence_ref_model(item) for item in evidence or []],
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
    )


def _knowledge_edge_model(
    record: KnowledgeEdgeRecord, evidence: list[EvidenceRefRecord] | None = None,
) -> KnowledgeEdge:
    return KnowledgeEdge(
        id=record.id, workspace_id=record.workspace_id,
        source_node_id=record.source_node_id, target_node_id=record.target_node_id,
        relation=record.relation, metadata=dict(record.metadata_json or {}),
        evidence=[_evidence_ref_model(item) for item in evidence or []],
        created_at=record.created_at.isoformat(),
    )


def _reasoning_run_model(
    record: ReasoningRunRecord, inputs: list[ReasoningRunInputRecord],
    outputs: list[ReasoningRunOutputRecord],
) -> ReasoningRun:
    return ReasoningRun(
        id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
        operator=record.operator, status=record.status, prompt=record.prompt,
        metadata=dict(record.metadata_json or {}),
        inputs=[ReasoningRunLink(knowledge_node_id=item.knowledge_node_id, ordinal=item.ordinal) for item in inputs],
        outputs=[ReasoningRunLink(knowledge_node_id=item.knowledge_node_id, ordinal=item.ordinal) for item in outputs],
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
    )


def _node_feedback_model(record: NodeFeedbackRecord) -> NodeFeedback:
    return NodeFeedback(
        id=record.id, workspace_id=record.workspace_id,
        knowledge_node_id=record.knowledge_node_id, user_id=record.user_id,
        verdict=record.verdict, rating=record.rating, comment=record.comment,
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
    )


def _canvas_layout_model(record: CanvasLayoutRecord) -> CanvasLayout:
    return CanvasLayout(
        id=record.id, workspace_id=record.workspace_id, canvas_id=record.canvas_id,
        knowledge_node_id=record.knowledge_node_id, x=record.x, y=record.y,
        width=record.width, height=record.height, z_index=record.z_index,
        collapsed=record.collapsed, updated_at=record.updated_at.isoformat(),
    )


class PaperStore:
    """Transactional paper repository backed by an explicitly configured SQLAlchemy database."""

    def __init__(self, database_url: str | None = None, *, session_factory: sessionmaker | None = None):
        if session_factory is None:
            if not database_url:
                raise ValueError("database_url or session_factory is required")
            self.engine = create_database_engine(database_url)
            self.session_factory = create_session_factory(self.engine)
        else:
            self.engine = None
            self.session_factory = session_factory

    def create_schema(self) -> None:
        if self.engine is None:
            bind = self.session_factory.kw.get("bind")
            if bind is None:
                raise RuntimeError("session factory has no bound engine")
            Base.metadata.create_all(bind)
        else:
            Base.metadata.create_all(self.engine)

    def ping(self) -> None:
        with self.session_factory() as session:
            session.execute(text("SELECT 1"))

    def ensure_user(self, principal: Principal) -> tuple[User, Workspace]:
        """Provision an identity and its personal workspace atomically on first use."""
        with self.session_factory() as session:
            existing = session.scalar(
                select(UserRecord).where(
                    UserRecord.issuer == principal.issuer,
                    UserRecord.subject == principal.subject,
                )
            )
            if existing is not None:
                personal = session.scalar(
                    select(WorkspaceRecord).where(WorkspaceRecord.personal_owner_id == existing.id)
                )
                membership = session.scalar(
                    select(WorkspaceMemberRecord).where(
                        WorkspaceMemberRecord.workspace_id == personal.id,
                        WorkspaceMemberRecord.user_id == existing.id,
                    )
                ) if personal else None
                if personal is None or membership is None:
                    raise RuntimeError("personal workspace invariant is broken")
                changed = False
                if principal.email and existing.email != principal.email:
                    existing.email = principal.email
                    changed = True
                if principal.display_name and existing.display_name != principal.display_name:
                    existing.display_name = principal.display_name
                    changed = True
                if changed:
                    session.commit()
                return _user_model(existing), _workspace_model(personal, membership.role)

        now = datetime.now(timezone.utc)
        user_id = str(uuid4())
        workspace_id = str(uuid4())
        user = UserRecord(
            id=user_id,
            issuer=principal.issuer,
            subject=principal.subject,
            email=principal.email,
            display_name=principal.display_name,
            created_at=now,
        )
        label = principal.display_name or principal.email or principal.subject
        personal = WorkspaceRecord(
            id=workspace_id,
            name=f"{label} のワークスペース",
            is_personal=True,
            personal_owner_id=user_id,
            created_by=user_id,
            created_at=now,
        )
        membership = WorkspaceMemberRecord(
            workspace_id=workspace_id, user_id=user_id, role="owner", created_at=now
        )
        try:
            with self.session_factory.begin() as session:
                session.add(user)
                session.flush()
                session.add(personal)
                session.flush()
                session.add(membership)
        except IntegrityError as exc:
            # A concurrent first request may have provisioned the same OIDC subject.
            # Only recover when that identity now exists; unrelated constraint
            # violations must remain visible instead of being retried forever.
            with self.session_factory() as session:
                concurrent = session.scalar(
                    select(UserRecord).where(
                        UserRecord.issuer == principal.issuer,
                        UserRecord.subject == principal.subject,
                    )
                )
                if concurrent is None:
                    raise
                personal = session.scalar(
                    select(WorkspaceRecord).where(
                        WorkspaceRecord.personal_owner_id == concurrent.id
                    )
                )
                membership = session.scalar(
                    select(WorkspaceMemberRecord).where(
                        WorkspaceMemberRecord.workspace_id == personal.id,
                        WorkspaceMemberRecord.user_id == concurrent.id,
                    )
                ) if personal else None
                if personal is None or membership is None:
                    raise RuntimeError("personal workspace invariant is broken") from exc
                return _user_model(concurrent), _workspace_model(personal, membership.role)
        return _user_model(user), _workspace_model(personal, "owner")

    def list_workspaces(self, user_id: str) -> list[Workspace]:
        with self.session_factory() as session:
            rows = session.execute(
                select(WorkspaceRecord, WorkspaceMemberRecord.role)
                .join(
                    WorkspaceMemberRecord,
                    WorkspaceMemberRecord.workspace_id == WorkspaceRecord.id,
                )
                .where(WorkspaceMemberRecord.user_id == user_id)
                .order_by(WorkspaceRecord.created_at, WorkspaceRecord.id)
            ).all()
            return [_workspace_model(record, role) for record, role in rows]

    def create_workspace(self, user_id: str, name: str) -> Workspace:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("workspace name is required")
        now = datetime.now(timezone.utc)
        record = WorkspaceRecord(
            id=str(uuid4()),
            name=cleaned,
            is_personal=False,
            personal_owner_id=None,
            created_by=user_id,
            created_at=now,
        )
        membership = WorkspaceMemberRecord(
            workspace_id=record.id, user_id=user_id, role="owner", created_at=now
        )
        with self.session_factory.begin() as session:
            if session.get(UserRecord, user_id) is None:
                raise WorkspaceAccessError(user_id)
            session.add(record)
            session.flush()
            session.add(membership)
        return _workspace_model(record, "owner")

    def rename_workspace(self, user_id: str, workspace_id: str, name: str) -> Workspace:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("workspace name is required")
        with self.session_factory.begin() as session:
            row = session.execute(
                select(WorkspaceRecord, WorkspaceMemberRecord.role)
                .join(
                    WorkspaceMemberRecord,
                    WorkspaceMemberRecord.workspace_id == WorkspaceRecord.id,
                )
                .where(
                    WorkspaceRecord.id == workspace_id,
                    WorkspaceMemberRecord.user_id == user_id,
                )
            ).one_or_none()
            if row is None:
                raise WorkspaceAccessError(workspace_id)
            record, role = row
            if role != "owner":
                raise WorkspacePermissionError(workspace_id)
            record.name = cleaned
            session.flush()
            return _workspace_model(record, role)

    def resolve_workspace(self, user_id: str, workspace_id: str | None) -> Workspace:
        with self.session_factory() as session:
            statement = (
                select(WorkspaceRecord, WorkspaceMemberRecord.role)
                .join(
                    WorkspaceMemberRecord,
                    WorkspaceMemberRecord.workspace_id == WorkspaceRecord.id,
                )
                .where(WorkspaceMemberRecord.user_id == user_id)
            )
            if workspace_id:
                statement = statement.where(WorkspaceRecord.id == workspace_id)
            else:
                statement = statement.where(WorkspaceRecord.personal_owner_id == user_id)
            row = session.execute(statement).one_or_none()
            if row is None:
                raise WorkspaceAccessError(workspace_id or "personal")
            return _workspace_model(row[0], row[1])

    def add_workspace_member(self, workspace_id: str, user_id: str, role: str = "viewer") -> None:
        """Small repository primitive used by authorization tests and future invitations."""
        if role not in {"owner", "editor", "viewer"}:
            raise ValueError("invalid workspace role")
        with self.session_factory.begin() as session:
            session.add(WorkspaceMemberRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                role=role,
                created_at=datetime.now(timezone.utc),
            ))

    def list(self, workspace_id: str) -> list[Paper]:
        with self.session_factory() as session:
            records = session.scalars(
                select(PaperRecord)
                .where(PaperRecord.workspace_id == workspace_id)
                .options(selectinload(PaperRecord.chunks))
            ).all()
            return [_to_model(record) for record in records]

    def get_by_hash(self, workspace_id: str, content_hash: str) -> Paper | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(PaperRecord)
                .where(PaperRecord.workspace_id == workspace_id, PaperRecord.content_hash == content_hash)
                .options(selectinload(PaperRecord.chunks))
            )
            return _to_model(record) if record else None

    def begin_processing(self, paper: Paper) -> Paper:
        paper.status = "processing"
        paper.error_message = None
        try:
            with self.session_factory.begin() as session:
                session.add(_record_from_model(paper))
        except IntegrityError as exc:
            duplicate = self.get_by_hash(paper.workspace_id, paper.content_hash or "")
            if duplicate is not None:
                raise DuplicatePaperError(duplicate) from exc
            raise
        return paper

    def mark_ready(
        self, paper: Paper, embedding_model: str | None = None,
        embedding_provider: str | None = None,
    ) -> Paper:
        paper.status = "ready"
        paper.error_message = None
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(PaperRecord).where(PaperRecord.id == paper.id).with_for_update()
            )
            if record is None:
                raise PaperNotFoundError(paper.id)
            record.title = paper.title
            record.authors = list(paper.authors)
            record.year = paper.year
            record.abstract = paper.abstract
            record.source = paper.source
            record.external_id = paper.external_id
            record.status = paper.status
            record.page_count = paper.page_count
            record.error_message = None
            record.storage_key = paper.storage_key
            record.mime_type = paper.mime_type
            record.byte_size = paper.byte_size
            session.execute(delete(ChunkRecord).where(ChunkRecord.paper_id == paper.id))
            session.add_all([
                ChunkRecord(
                    id=chunk.id,
                    paper_id=paper.id,
                    page=chunk.page,
                    section=chunk.section,
                    text=chunk.text,
                )
                for chunk in paper.chunks
            ])
            _queue_embedding_job(
                session, paper_id=paper.id, workspace_id=paper.workspace_id,
                provider=embedding_provider or os.getenv("EMBEDDING_PROVIDER", "openai"),
                model=embedding_model or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
                total_chunks=len(paper.chunks),
            )
        return paper

    def mark_failed(self, paper_id: str, error_message: str, *, clear_storage: bool = False) -> Paper:
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(PaperRecord)
                .where(PaperRecord.id == paper_id)
                .with_for_update()
            )
            if record is None:
                raise PaperNotFoundError(paper_id)
            record.status = "failed"
            record.error_message = error_message[:2000]
            if clear_storage:
                record.storage_key = None
                record.mime_type = None
                record.byte_size = None
        return self.get(paper_id)

    def upsert(
        self, paper: Paper, embedding_model: str | None = None,
        embedding_provider: str | None = None,
    ) -> Paper:
        """Compatibility path for external imports; insert atomically and deduplicate by hash."""
        existing = self.get_by_hash(paper.workspace_id, paper.content_hash or "") if paper.content_hash else None
        if existing:
            raise DuplicatePaperError(existing)
        paper.status = "ready"
        try:
            with self.session_factory.begin() as session:
                session.add(_record_from_model(paper))
                _queue_embedding_job(
                    session, paper_id=paper.id, workspace_id=paper.workspace_id,
                    provider=embedding_provider or os.getenv("EMBEDDING_PROVIDER", "openai"),
                    model=embedding_model or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
                    total_chunks=len(paper.chunks),
                )
        except IntegrityError as exc:
            duplicate = self.get_by_hash(paper.workspace_id, paper.content_hash or "")
            if duplicate is not None:
                raise DuplicatePaperError(duplicate) from exc
            raise
        return paper

    def get(self, paper_id: str) -> Paper:
        with self.session_factory() as session:
            record = session.scalar(
                select(PaperRecord)
                .where(PaperRecord.id == paper_id)
                .options(selectinload(PaperRecord.chunks))
            )
            if record is None:
                raise PaperNotFoundError(paper_id)
            return _to_model(record)

    def get_owned(self, workspace_id: str, paper_id: str) -> Paper:
        with self.session_factory() as session:
            record = session.scalar(
                select(PaperRecord)
                .where(PaperRecord.workspace_id == workspace_id, PaperRecord.id == paper_id)
                .options(selectinload(PaperRecord.chunks))
            )
            if record is None:
                raise PaperNotFoundError(paper_id)
            return _to_model(record)

    def get_page(self, workspace_id: str, paper_id: str, page: int) -> tuple[Paper, list[Chunk]]:
        paper = self.get_owned(workspace_id, paper_id)
        return paper, [chunk for chunk in paper.chunks if chunk.page == page]

    def get_chunk(self, workspace_id: str, paper_id: str, chunk_id: str) -> Chunk:
        paper = self.get_owned(workspace_id, paper_id)
        for chunk in paper.chunks:
            if chunk.id == chunk_id:
                return chunk
        raise PaperNotFoundError(chunk_id)

    def delete(self, workspace_id: str, paper_id: str) -> Paper | None:
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(PaperRecord)
                .where(PaperRecord.workspace_id == workspace_id, PaperRecord.id == paper_id)
                .options(selectinload(PaperRecord.chunks))
            )
            if record is None:
                return None
            has_grounded_graph_reference = session.scalar(
                select(EvidenceRefRecord.id)
                .join(SourceSpanRecord, EvidenceRefRecord.source_span_id == SourceSpanRecord.id)
                .join(SourceVersionRecord, SourceSpanRecord.source_version_id == SourceVersionRecord.id)
                .where(
                    SourceVersionRecord.workspace_id == workspace_id,
                    SourceVersionRecord.paper_id == paper_id,
                )
                .limit(1)
            )
            if has_grounded_graph_reference is not None:
                raise ResourceConflictError("paper is retained because it grounds knowledge graph evidence")
            paper = _to_model(record)
            session.delete(record)
            return paper

    def list_tags(self, workspace_id: str) -> list[Tag]:
        with self.session_factory() as session:
            rows = session.scalars(select(TagRecord).where(TagRecord.workspace_id == workspace_id).order_by(TagRecord.name)).all()
            return [Tag(id=r.id, name=r.name, color=r.color, created_at=r.created_at.isoformat()) for r in rows]

    def create_tag(self, workspace_id: str, name: str, color: str) -> Tag:
        record = TagRecord(id=str(uuid4()), workspace_id=workspace_id, name=name.strip(), color=color.strip(), created_at=datetime.now(timezone.utc))
        try:
            with self.session_factory.begin() as session:
                session.add(record)
        except IntegrityError as exc:
            raise ResourceConflictError("tag name already exists") from exc
        return Tag(id=record.id, name=record.name, color=record.color, created_at=record.created_at.isoformat())

    def update_tag(self, workspace_id: str, tag_id: str, name: str, color: str) -> Tag:
        try:
            with self.session_factory.begin() as session:
                record = session.scalar(select(TagRecord).where(TagRecord.workspace_id == workspace_id, TagRecord.id == tag_id))
                if record is None:
                    raise PaperNotFoundError(tag_id)
                record.name, record.color = name.strip(), color.strip()
        except IntegrityError as exc:
            raise ResourceConflictError("tag name already exists") from exc
        return Tag(id=record.id, name=record.name, color=record.color, created_at=record.created_at.isoformat())

    def delete_tag(self, workspace_id: str, tag_id: str) -> bool:
        with self.session_factory.begin() as session:
            result = session.execute(delete(TagRecord).where(TagRecord.workspace_id == workspace_id, TagRecord.id == tag_id))
            return bool(result.rowcount)

    def set_paper_tags(self, workspace_id: str, paper_id: str, tag_ids: list[str]) -> list[Tag]:
        unique_ids = list(dict.fromkeys(tag_ids))
        with self.session_factory.begin() as session:
            if session.scalar(select(PaperRecord.id).where(PaperRecord.workspace_id == workspace_id, PaperRecord.id == paper_id)) is None:
                raise PaperNotFoundError(paper_id)
            tags = session.scalars(select(TagRecord).where(TagRecord.workspace_id == workspace_id, TagRecord.id.in_(unique_ids))).all() if unique_ids else []
            if len(tags) != len(unique_ids):
                raise PaperNotFoundError("tag")
            session.execute(delete(PaperTagRecord).where(PaperTagRecord.paper_id == paper_id))
            session.add_all([PaperTagRecord(paper_id=paper_id, tag_id=tag_id) for tag_id in unique_ids])
            return [Tag(id=r.id, name=r.name, color=r.color, created_at=r.created_at.isoformat()) for r in tags]

    def get_paper_tags(self, workspace_id: str, paper_id: str) -> list[Tag]:
        with self.session_factory() as session:
            if session.scalar(select(PaperRecord.id).where(PaperRecord.workspace_id == workspace_id, PaperRecord.id == paper_id)) is None:
                raise PaperNotFoundError(paper_id)
            rows = session.scalars(select(TagRecord).join(PaperTagRecord, PaperTagRecord.tag_id == TagRecord.id).where(PaperTagRecord.paper_id == paper_id).order_by(TagRecord.name)).all()
            return [Tag(id=r.id, name=r.name, color=r.color, created_at=r.created_at.isoformat()) for r in rows]

    @staticmethod
    def _note_model(r: NoteRecord) -> Note:
        return Note(id=r.id, paper_id=r.paper_id, author_id=r.author_id, title=r.title, content=r.content, created_at=r.created_at.isoformat(), updated_at=r.updated_at.isoformat())

    def list_notes(self, workspace_id: str, paper_id: str | None = None) -> list[Note]:
        with self.session_factory() as session:
            stmt = select(NoteRecord).where(NoteRecord.workspace_id == workspace_id)
            if paper_id is not None:
                stmt = stmt.where(NoteRecord.paper_id == paper_id)
            return [self._note_model(r) for r in session.scalars(stmt.order_by(NoteRecord.updated_at.desc())).all()]

    def create_note(self, workspace_id: str, author_id: str, paper_id: str | None, title: str, content: str) -> Note:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            if paper_id and session.scalar(select(PaperRecord.id).where(PaperRecord.workspace_id == workspace_id, PaperRecord.id == paper_id)) is None:
                raise PaperNotFoundError(paper_id)
            record = NoteRecord(id=str(uuid4()), workspace_id=workspace_id, paper_id=paper_id, author_id=author_id, title=title.strip(), content=content, created_at=now, updated_at=now)
            session.add(record)
        return self._note_model(record)

    def update_note(self, workspace_id: str, note_id: str, title: str | None, content: str | None) -> Note:
        with self.session_factory.begin() as session:
            record = session.scalar(select(NoteRecord).where(NoteRecord.workspace_id == workspace_id, NoteRecord.id == note_id))
            if record is None:
                raise PaperNotFoundError(note_id)
            if title is not None: record.title = title.strip()
            if content is not None: record.content = content
            record.updated_at = datetime.now(timezone.utc)
        return self._note_model(record)

    def delete_note(self, workspace_id: str, note_id: str) -> bool:
        with self.session_factory.begin() as session:
            result = session.execute(delete(NoteRecord).where(NoteRecord.workspace_id == workspace_id, NoteRecord.id == note_id))
            return bool(result.rowcount)

    def add_search_history(self, workspace_id: str, user_id: str, query: str, paper_ids: list[str], result_summary: dict) -> SearchHistory:
        record = SearchHistoryRecord(id=str(uuid4()), workspace_id=workspace_id, user_id=user_id, query=query, paper_ids=paper_ids, result_summary=result_summary, created_at=datetime.now(timezone.utc))
        with self.session_factory.begin() as session: session.add(record)
        return SearchHistory(id=record.id, user_id=user_id, query=query, paper_ids=paper_ids, result_summary=result_summary, created_at=record.created_at.isoformat())

    def list_search_history(self, workspace_id: str) -> list[SearchHistory]:
        with self.session_factory() as session:
            rows = session.scalars(select(SearchHistoryRecord).where(SearchHistoryRecord.workspace_id == workspace_id).order_by(SearchHistoryRecord.created_at.desc())).all()
            return [SearchHistory(id=r.id, user_id=r.user_id, query=r.query, paper_ids=r.paper_ids, result_summary=r.result_summary, created_at=r.created_at.isoformat()) for r in rows]

    def delete_search_history(self, workspace_id: str, history_id: str) -> bool:
        with self.session_factory.begin() as session:
            return bool(session.execute(delete(SearchHistoryRecord).where(SearchHistoryRecord.workspace_id == workspace_id, SearchHistoryRecord.id == history_id)).rowcount)

    def get_chunk_embeddings(self, workspace_id: str, chunk_ids: list[str], model: str) -> dict[str, list[float]]:
        if not chunk_ids:
            return {}
        with self.session_factory() as session:
            rows = session.execute(
                select(ChunkEmbeddingRecord.chunk_id, ChunkEmbeddingRecord.vector)
                .join(ChunkRecord, ChunkRecord.id == ChunkEmbeddingRecord.chunk_id)
                .join(PaperRecord, PaperRecord.id == ChunkRecord.paper_id)
                .where(
                    PaperRecord.workspace_id == workspace_id,
                    ChunkEmbeddingRecord.chunk_id.in_(chunk_ids),
                    ChunkEmbeddingRecord.model == model,
                )
            ).all()
            return {chunk_id: list(vector) for chunk_id, vector in rows}

    def upsert_chunk_embeddings(self, workspace_id: str, model: str, embeddings: dict[str, list[float]]) -> None:
        if not embeddings:
            return
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            allowed = set(session.scalars(
                select(ChunkRecord.id)
                .join(PaperRecord, PaperRecord.id == ChunkRecord.paper_id)
                .where(PaperRecord.workspace_id == workspace_id, ChunkRecord.id.in_(embeddings))
            ).all())
            values = [
                {
                    "chunk_id": chunk_id,
                    "model": model,
                    "dimensions": len(vector),
                    "vector": vector,
                    "updated_at": now,
                }
                for chunk_id, vector in embeddings.items()
                if chunk_id in allowed
            ]
            if not values:
                return

            dialect_name = session.get_bind().dialect.name
            if dialect_name == "postgresql":
                statement = postgresql_insert(ChunkEmbeddingRecord).values(values)
                statement = statement.on_conflict_do_update(
                    index_elements=[ChunkEmbeddingRecord.chunk_id],
                    set_={
                        "model": statement.excluded.model,
                        "dimensions": statement.excluded.dimensions,
                        "vector": statement.excluded.vector,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
                session.execute(statement)
            elif dialect_name == "sqlite":
                statement = sqlite_insert(ChunkEmbeddingRecord).values(values)
                statement = statement.on_conflict_do_update(
                    index_elements=[ChunkEmbeddingRecord.chunk_id],
                    set_={
                        "model": statement.excluded.model,
                        "dimensions": statement.excluded.dimensions,
                        "vector": statement.excluded.vector,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
                session.execute(statement)
            else:
                # Tests and supported deployments use SQLite or PostgreSQL. Keep a
                # conservative fallback for other SQLAlchemy dialects.
                for value in values:
                    record = session.get(ChunkEmbeddingRecord, value["chunk_id"])
                    if record is None:
                        session.add(ChunkEmbeddingRecord(**value))
                    else:
                        record.model = value["model"]
                        record.dimensions = value["dimensions"]
                        record.vector = value["vector"]
                        record.updated_at = value["updated_at"]

    @staticmethod
    def _conversation_model(record: ResearchConversationRecord) -> ResearchConversation:
        return ResearchConversation(
            id=record.id, title=record.title, summary=record.summary, created_by=record.created_by,
            message_count=record.message_count, memory_event_count=record.memory_event_count,
            created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
        )

    @staticmethod
    def _message_model(record: ResearchMessageRecord) -> ResearchMessage:
        return ResearchMessage(
            id=record.id, conversation_id=record.conversation_id, role=record.role,
            ordinal=record.ordinal,
            content=record.content,
            citations=[Citation.model_validate(item) for item in (record.citations or [])],
            created_at=record.created_at.isoformat(),
        )

    @staticmethod
    def _memory_event_model(record: ResearchMemoryEventRecord) -> ResearchMemoryEvent:
        return ResearchMemoryEvent(
            id=record.id, conversation_id=record.conversation_id,
            source_message_id=record.source_message_id, ordinal=record.ordinal,
            kind=record.kind, content=record.content,
            created_at=record.created_at.isoformat(),
        )

    @staticmethod
    def _scoped_conversation_record(
        session: Session, workspace_id: str, conversation_id: str, *, lock: bool = False,
    ) -> ResearchConversationRecord:
        statement = select(ResearchConversationRecord).where(
            ResearchConversationRecord.workspace_id == workspace_id,
            ResearchConversationRecord.id == conversation_id,
        )
        if lock:
            statement = statement.with_for_update()
        record = session.scalar(statement)
        if record is None:
            raise PaperNotFoundError(conversation_id)
        return record

    @staticmethod
    def _ordinal_page_rows(session: Session, statement, ordinal_column, limit: int):
        bounded_limit = max(1, min(limit, 200))
        rows = list(session.scalars(
            statement.order_by(ordinal_column.desc()).limit(bounded_limit + 1)
        ).all())
        has_more = len(rows) > bounded_limit
        selected = rows[:bounded_limit]
        return selected, selected[-1].ordinal if has_more else None

    def create_conversation(self, workspace_id: str, user_id: str, title: str) -> ResearchConversation:
        now = datetime.now(timezone.utc)
        record = ResearchConversationRecord(
            id=str(uuid4()), workspace_id=workspace_id, created_by=user_id,
            title=title.strip(), summary="", message_count=0, memory_event_count=0,
            created_at=now, updated_at=now,
        )
        with self.session_factory.begin() as session:
            session.add(record)
        return self._conversation_model(record)

    def list_conversations(self, workspace_id: str) -> list[ResearchConversation]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ResearchConversationRecord)
                .where(ResearchConversationRecord.workspace_id == workspace_id)
                .order_by(ResearchConversationRecord.updated_at.desc())
            ).all()
            return [self._conversation_model(row) for row in rows]

    def get_conversation(self, workspace_id: str, conversation_id: str) -> ResearchConversationDetail:
        with self.session_factory() as session:
            record = self._scoped_conversation_record(session, workspace_id, conversation_id)
            # Compatibility detail stays bounded; older turns are available via
            # list_research_messages_page and message_count indicates truncation.
            messages = list(session.scalars(select(ResearchMessageRecord).where(
                ResearchMessageRecord.conversation_id == conversation_id
            ).order_by(ResearchMessageRecord.ordinal.desc()).limit(100)).all())
            base = self._conversation_model(record)
            return ResearchConversationDetail(
                **base.model_dump(),
                messages=[self._message_model(item) for item in reversed(messages)],
            )

    def get_conversation_metadata(
        self, workspace_id: str, conversation_id: str,
    ) -> ResearchConversation:
        """Load conversation continuity fields without materializing messages."""
        with self.session_factory() as session:
            return self._conversation_model(
                self._scoped_conversation_record(session, workspace_id, conversation_id)
            )

    def add_research_exchange(
        self, workspace_id: str, conversation_id: str, query: str, answer: str,
        citations: list[Citation], memory_delta: dict | None = None,
    ) -> ResearchConversationDetail:
        self.record_research_exchange(
            workspace_id, conversation_id, query, answer, citations,
            memory_delta=memory_delta,
        )
        return self.get_conversation(workspace_id, conversation_id)

    def record_research_exchange(
        self, workspace_id: str, conversation_id: str, query: str, answer: str,
        citations: list[Citation], memory_delta: dict | None = None,
    ) -> None:
        """Persist one exchange atomically without reloading the conversation."""
        with self.session_factory.begin() as session:
            conversation = self._scoped_conversation_record(
                session, workspace_id, conversation_id, lock=True,
            )
            # Timestamp only after acquiring the conversation lock so message order
            # matches the serialized order used to update the durable memory.
            now = datetime.now(timezone.utc)
            first_ordinal = conversation.message_count + 1
            assistant_message_id = str(uuid4())
            session.add_all([
                ResearchMessageRecord(id=str(uuid4()), conversation_id=conversation_id, ordinal=first_ordinal, role="user", content=query, citations=[], created_at=now),
                ResearchMessageRecord(id=assistant_message_id, conversation_id=conversation_id, ordinal=first_ordinal + 1, role="assistant", content=answer, citations=[c.model_dump(mode="json") for c in citations], created_at=now + timedelta(microseconds=1)),
            ])
            conversation.message_count += 2
            self._append_memory_events(
                session, conversation=conversation, source_message_id=assistant_message_id,
                memory_delta=memory_delta, created_at=now + timedelta(microseconds=2),
            )
            # Compute from the row locked above, rather than from the snapshot read
            # before the LLM call. Concurrent turns therefore append to the latest
            # committed memory instead of overwriting one another.
            from .rag import update_memory

            conversation.summary = update_memory(
                conversation.summary, query, answer, memory_delta=memory_delta,
            )
            conversation.updated_at = now

    @staticmethod
    def _append_memory_events(
        session: Session, *, conversation: ResearchConversationRecord,
        source_message_id: str, memory_delta: dict | None, created_at: datetime,
    ) -> None:
        if not isinstance(memory_delta, dict):
            return
        kind_by_key = {
            "hypotheses": "hypothesis",
            "assumptions": "assumption",
            "unresolved_questions": "unresolved_question",
            "planned_tests": "planned_test",
        }
        candidates: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for key, kind in kind_by_key.items():
            values = memory_delta.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, str):
                    continue
                content = " ".join(value.split()).strip()[:2000]
                if not content:
                    continue
                content_hash = hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()
                identity = (kind, content_hash)
                if identity not in seen:
                    seen.add(identity)
                    candidates.append((kind, content, content_hash))
        if not candidates:
            return

        existing = set(session.execute(
            select(ResearchMemoryEventRecord.kind, ResearchMemoryEventRecord.content_hash).where(
                ResearchMemoryEventRecord.conversation_id == conversation.id,
                ResearchMemoryEventRecord.content_hash.in_([item[2] for item in candidates]),
            )
        ).all())
        new_items = [item for item in candidates if (item[0], item[2]) not in existing]
        first_ordinal = conversation.memory_event_count + 1
        for offset, (kind, content, content_hash) in enumerate(new_items):
            session.add(ResearchMemoryEventRecord(
                id=str(uuid4()), workspace_id=conversation.workspace_id,
                conversation_id=conversation.id, source_message_id=source_message_id,
                ordinal=first_ordinal + offset, kind=kind, content=content,
                content_hash=content_hash,
                created_at=created_at + timedelta(microseconds=offset),
            ))
        conversation.memory_event_count += len(new_items)

    def list_research_messages_page(
        self, workspace_id: str, conversation_id: str, *, limit: int = 100,
        before_ordinal: int | None = None,
    ) -> ResearchMessagePage:
        with self.session_factory() as session:
            self._scoped_conversation_record(session, workspace_id, conversation_id)
            statement = select(ResearchMessageRecord).where(
                ResearchMessageRecord.conversation_id == conversation_id
            )
            if before_ordinal is not None:
                statement = statement.where(ResearchMessageRecord.ordinal < before_ordinal)
            selected, next_cursor = self._ordinal_page_rows(
                session, statement, ResearchMessageRecord.ordinal, limit,
            )
            items = [self._message_model(row) for row in reversed(selected)]
            return ResearchMessagePage(
                items=items, next_before_ordinal=next_cursor,
            )

    def list_research_memory_page(
        self, workspace_id: str, conversation_id: str, *, kind: str | None = None,
        limit: int = 100, before_ordinal: int | None = None,
    ) -> ResearchMemoryPage:
        allowed_kinds = {"hypothesis", "assumption", "unresolved_question", "planned_test"}
        if kind is not None and kind not in allowed_kinds:
            raise ValueError(f"unsupported research memory kind: {kind}")
        with self.session_factory() as session:
            self._scoped_conversation_record(session, workspace_id, conversation_id)
            statement = select(ResearchMemoryEventRecord).where(
                ResearchMemoryEventRecord.conversation_id == conversation_id
            )
            if kind is not None:
                statement = statement.where(ResearchMemoryEventRecord.kind == kind)
            if before_ordinal is not None:
                statement = statement.where(ResearchMemoryEventRecord.ordinal < before_ordinal)
            selected, next_cursor = self._ordinal_page_rows(
                session, statement, ResearchMemoryEventRecord.ordinal, limit,
            )
            items = [self._memory_event_model(row) for row in reversed(selected)]
            return ResearchMemoryPage(
                items=items, next_before_ordinal=next_cursor,
            )

    def search_research_memory(
        self, workspace_id: str, conversation_id: str, query: str, *, limit: int = 20,
    ) -> list[ResearchMemoryEvent]:
        """Return a bounded set of query-relevant durable memories.

        Matching and limiting are performed by the database; this method never
        materializes the full conversation memory in Python. Recent events break
        equal keyword scores so evolving research context is preferred.
        """
        limit = max(1, min(limit, 100))
        normalized_query = " ".join(query.split()).strip()[:500]
        terms: list[str] = []
        if normalized_query:
            terms.append(normalized_query)
            for token in re.findall(r"[^\W_]{2,}", normalized_query, flags=re.UNICODE):
                folded = token.casefold()
                if folded not in {item.casefold() for item in terms}:
                    terms.append(token)
                if len(terms) >= 8:
                    break

        with self.session_factory() as session:
            self._scoped_conversation_record(session, workspace_id, conversation_id)

            statement = select(ResearchMemoryEventRecord).where(
                ResearchMemoryEventRecord.conversation_id == conversation_id
            )
            if terms:
                # Escape SQL wildcard characters before constructing LIKE
                # patterns so user text cannot broaden the candidate set.
                escaped_terms = [
                    term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    for term in terms
                ]
                matches = [
                    ResearchMemoryEventRecord.content.ilike(f"%{term}%", escape="\\")
                    for term in escaped_terms
                ]
                relevance = sum(
                    (case((match, len(matches) - index), else_=0)
                     for index, match in enumerate(matches)),
                    start=0,
                )
                statement = statement.where(or_(*matches)).order_by(
                    relevance.desc(), ResearchMemoryEventRecord.ordinal.desc()
                )
            else:
                statement = statement.order_by(ResearchMemoryEventRecord.ordinal.desc())
            rows = list(session.scalars(statement.limit(limit)).all())
            if terms and not rows:
                # Japanese research questions often have no whitespace, so a
                # purely lexical LIKE query can miss a clearly relevant prior
                # event. Fall back to the most recent bounded slice rather than
                # loading the full history or silently dropping all memory.
                rows = list(session.scalars(
                    select(ResearchMemoryEventRecord).where(
                        ResearchMemoryEventRecord.conversation_id == conversation_id
                    ).order_by(ResearchMemoryEventRecord.ordinal.desc()).limit(limit)
                ).all())
            return [self._memory_event_model(row) for row in rows]

    def save_comparison(self, workspace_id: str, user_id: str, name: str, paper_ids: list[str], result: list[dict]) -> SavedComparison:
        record = SavedComparisonRecord(id=str(uuid4()), workspace_id=workspace_id, user_id=user_id, name=name.strip(), paper_ids=paper_ids, result=result, created_at=datetime.now(timezone.utc))
        with self.session_factory.begin() as session: session.add(record)
        return SavedComparison(id=record.id, user_id=user_id, name=record.name, paper_ids=paper_ids, result=result, created_at=record.created_at.isoformat())

    def list_comparisons(self, workspace_id: str) -> list[SavedComparison]:
        with self.session_factory() as session:
            rows = session.scalars(select(SavedComparisonRecord).where(SavedComparisonRecord.workspace_id == workspace_id).order_by(SavedComparisonRecord.created_at.desc())).all()
            return [SavedComparison(id=r.id, user_id=r.user_id, name=r.name, paper_ids=r.paper_ids, result=r.result, created_at=r.created_at.isoformat()) for r in rows]

    def delete_comparison(self, workspace_id: str, comparison_id: str) -> bool:
        with self.session_factory.begin() as session:
            return bool(session.execute(delete(SavedComparisonRecord).where(SavedComparisonRecord.workspace_id == workspace_id, SavedComparisonRecord.id == comparison_id)).rowcount)

    def create_ingestion_job(self, workspace_id: str, paper_id: str) -> IngestionJob:
        now = datetime.now(timezone.utc)
        record = IngestionJobRecord(id=str(uuid4()), workspace_id=workspace_id, paper_id=paper_id, status="queued", progress=0, attempts=0, error_message=None, created_at=now, updated_at=now)
        try:
            with self.session_factory.begin() as session: session.add(record)
        except IntegrityError:
            with self.session_factory() as session:
                existing = session.scalar(select(IngestionJobRecord).where(IngestionJobRecord.paper_id == paper_id))
                if existing is None: raise
                return _job_model(existing)
        return _job_model(record)

    def get_ingestion_job(self, workspace_id: str, job_id: str) -> IngestionJob:
        with self.session_factory() as session:
            record = session.scalar(select(IngestionJobRecord).where(IngestionJobRecord.workspace_id == workspace_id, IngestionJobRecord.id == job_id))
            if record is None: raise PaperNotFoundError(job_id)
            return _job_model(record)

    def claim_ingestion_job(self, job_id: str, paper_id: str, max_attempts: int, lease_seconds: int = 300) -> IngestionJob | None:
        with self.session_factory.begin() as session:
            record = session.scalar(select(IngestionJobRecord).where(IngestionJobRecord.id == job_id, IngestionJobRecord.paper_id == paper_id).with_for_update())
            if record is None: raise PaperNotFoundError(job_id)
            now = datetime.now(timezone.utc)
            updated_at = record.updated_at if record.updated_at.tzinfo else record.updated_at.replace(tzinfo=timezone.utc)
            stale = now - updated_at > timedelta(seconds=lease_seconds)
            if record.status == "succeeded" or (record.status == "running" and not stale): return None
            if record.attempts >= max_attempts:
                record.status, record.error_message, record.updated_at = "failed", "ingestion retry limit exceeded", now
                paper = session.get(PaperRecord, paper_id)
                if paper is not None: paper.status, paper.error_message = "failed", record.error_message
                return None
            record.status, record.progress, record.error_message = "running", 1, None
            record.attempts += 1; record.updated_at = now
            paper = session.get(PaperRecord, paper_id)
            if paper is not None: paper.status, paper.error_message = "processing", None
        return _job_model(record)

    def heartbeat_ingestion_job(self, job_id: str, paper_id: str, expected_attempt: int) -> bool:
        with self.session_factory.begin() as session:
            record = session.scalar(select(IngestionJobRecord).where(IngestionJobRecord.id == job_id, IngestionJobRecord.paper_id == paper_id, IngestionJobRecord.status == "running", IngestionJobRecord.attempts == expected_attempt))
            if record is None: return False
            record.updated_at = datetime.now(timezone.utc)
            return True

    def reap_ingestion_jobs(self, lease_seconds: int, max_attempts: int) -> list[tuple[str, str]]:
        """Fence stale workers and return (paper_id, job_id) pairs safe to enqueue."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=lease_seconds)
        queued: list[tuple[str, str]] = []
        with self.session_factory.begin() as session:
            records = session.scalars(
                select(IngestionJobRecord)
                .where(or_(
                    IngestionJobRecord.status == "queued",
                    (IngestionJobRecord.status == "running") & (IngestionJobRecord.updated_at < cutoff),
                ))
                .with_for_update(skip_locked=True)
            ).all()
            for record in records:
                paper = session.get(PaperRecord, record.paper_id)
                if record.attempts >= max_attempts:
                    record.status, record.error_message, record.updated_at = "failed", "ingestion retry limit exceeded after worker loss", now
                    if paper is not None: paper.status, paper.error_message = "failed", record.error_message
                    continue
                record.status, record.progress, record.error_message, record.updated_at = "queued", 0, "worker lease expired; queued for recovery" if record.attempts else None, now
                if paper is not None: paper.status, paper.error_message = "processing", None
                queued.append((record.paper_id, record.id))
        return queued

    def update_ingestion_progress(self, job_id: str, progress: int, expected_attempt: int) -> bool:
        with self.session_factory.begin() as session:
            record = session.get(IngestionJobRecord, job_id)
            if record is None or record.status != "running" or record.attempts != expected_attempt: return False
            record.progress = max(record.progress, min(99, progress)); record.updated_at = datetime.now(timezone.utc)
            return True

    def complete_ingestion(
        self, job_id: str, paper: Paper, pages: list[PaperPage],
        elements: list[DocumentElement], expected_attempt: int,
        embedding_model: str = "text-embedding-3-small",
        embedding_provider: str = "openai",
    ) -> None:
        with self.session_factory.begin() as session:
            job = session.scalar(select(IngestionJobRecord).where(IngestionJobRecord.id == job_id, IngestionJobRecord.paper_id == paper.id).with_for_update())
            record = session.scalar(select(PaperRecord).where(PaperRecord.id == paper.id).with_for_update())
            if job is None or record is None: raise PaperNotFoundError(job_id)
            if job.status != "running" or job.attempts != expected_attempt: raise ResourceConflictError("ingestion lease was superseded")
            session.execute(delete(ChunkRecord).where(ChunkRecord.paper_id == paper.id)); session.execute(delete(PaperPageRecord).where(PaperPageRecord.paper_id == paper.id)); session.execute(delete(DocumentElementRecord).where(DocumentElementRecord.paper_id == paper.id))
            session.add_all([ChunkRecord(id=c.id, paper_id=paper.id, page=c.page, section=c.section, text=c.text) for c in paper.chunks])
            session.add_all([PaperPageRecord(paper_id=paper.id, page=p.page, text=p.text, text_source=p.text_source, quality=p.quality) for p in pages])
            session.add_all([DocumentElementRecord(id=e.id, paper_id=paper.id, page=e.page, kind=e.kind, bbox=e.bbox, text=e.text, structured_data=e.structured_data, asset_key=e.asset_key) for e in elements])
            self._ensure_paper_source_provenance(session, paper, pages)
            record.title, record.abstract, record.page_count, record.status, record.error_message = paper.title, paper.abstract, paper.page_count, "ready", None
            job.status, job.progress, job.error_message, job.updated_at = "succeeded", 100, None, datetime.now(timezone.utc)
            _queue_embedding_job(
                session, paper_id=paper.id, workspace_id=paper.workspace_id,
                provider=embedding_provider,
                model=embedding_model, total_chunks=len(paper.chunks),
            )

    @staticmethod
    def _ensure_paper_source_provenance(
        session: Session, paper: Paper, pages: list[PaperPage],
    ) -> None:
        """Seed page anchors once for every immutable uploaded paper version.

        Existing paper APIs continue to use chunks.  The graph layer gets its
        own stable page spans, so a later chunking strategy cannot invalidate a
        generated node's provenance.
        """
        if not paper.content_hash:
            return
        existing = session.scalar(select(SourceVersionRecord).where(
            SourceVersionRecord.workspace_id == paper.workspace_id,
            SourceVersionRecord.content_hash == paper.content_hash,
        ))
        if existing is not None:
            return
        source = SourceVersionRecord(
            id=str(uuid4()), workspace_id=paper.workspace_id, paper_id=paper.id,
            kind="paper", locator=paper.storage_key or f"paper:{paper.id}",
            content_hash=paper.content_hash,
            metadata_json={"mime_type": paper.mime_type, "title": paper.title},
            created_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()
        now = datetime.now(timezone.utc)
        session.add_all([SourceSpanRecord(
            id=str(uuid4()), workspace_id=paper.workspace_id, source_version_id=source.id,
            page=page.page, locator_json={"paper_id": paper.id, "text_source": page.text_source},
            text=page.text, created_at=now,
        ) for page in pages if page.text])

    def get_embedding_job(self, job_id: str) -> EmbeddingJob:
        with self.session_factory() as session:
            record = session.get(EmbeddingJobRecord, job_id)
            if record is None:
                raise PaperNotFoundError(job_id)
            return _embedding_job_model(record)

    def ensure_embedding_jobs(self, provider: str, model: str) -> int:
        """Backfill ready papers for the currently configured embedding identity."""
        created = 0
        with self.session_factory.begin() as session:
            rows = session.execute(
                select(PaperRecord.id, PaperRecord.workspace_id, func.count(ChunkRecord.id))
                .join(ChunkRecord, ChunkRecord.paper_id == PaperRecord.id)
                .where(PaperRecord.status == "ready")
                .group_by(PaperRecord.id, PaperRecord.workspace_id)
            ).all()
            existing = set(session.scalars(select(EmbeddingJobRecord.paper_id).where(
                EmbeddingJobRecord.provider == provider,
                EmbeddingJobRecord.model == model,
            )).all())
            for paper_id, workspace_id, total_chunks in rows:
                if paper_id in existing:
                    continue
                _queue_embedding_job(
                    session, paper_id=paper_id, workspace_id=workspace_id,
                    provider=provider, model=model, total_chunks=int(total_chunks),
                )
                created += 1
        return created

    def embedding_statuses(
        self, workspace_id: str, paper_ids: list[str], model: str,
    ) -> dict[str, str]:
        """Return per-paper readiness without loading chunks or vectors."""
        if not paper_ids:
            return {}
        with self.session_factory() as session:
            rows = session.execute(
                select(EmbeddingJobRecord.paper_id, EmbeddingJobRecord.status)
                .where(
                    EmbeddingJobRecord.workspace_id == workspace_id,
                    EmbeddingJobRecord.paper_id.in_(paper_ids),
                    EmbeddingJobRecord.model == model,
                )
            ).all()
            return dict(rows)

    def claim_embedding_job(
        self, job_id: str, max_attempts: int, lease_seconds: int = 300,
    ) -> EmbeddingJob | None:
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(EmbeddingJobRecord)
                .where(EmbeddingJobRecord.id == job_id)
                .with_for_update()
            )
            if record is None:
                raise PaperNotFoundError(job_id)
            now = datetime.now(timezone.utc)
            updated_at = record.updated_at if record.updated_at.tzinfo else record.updated_at.replace(tzinfo=timezone.utc)
            stale = now - updated_at > timedelta(seconds=lease_seconds)
            if record.status == "succeeded" or (record.status == "running" and not stale):
                return None
            if record.attempts >= max_attempts:
                record.status, record.error_code, record.updated_at = "failed", "retry_limit", now
                return None
            record.status, record.progress, record.error_code = "running", max(1, record.progress), None
            record.attempts += 1
            record.updated_at = now
            session.flush()
            return _embedding_job_model(record)

    def update_embedding_progress(
        self, job_id: str, completed_chunks: int, expected_attempt: int,
    ) -> bool:
        with self.session_factory.begin() as session:
            record = session.scalar(select(EmbeddingJobRecord).where(
                EmbeddingJobRecord.id == job_id,
                EmbeddingJobRecord.status == "running",
                EmbeddingJobRecord.attempts == expected_attempt,
            ).with_for_update())
            if record is None:
                return False
            record.completed_chunks = max(record.completed_chunks, min(record.total_chunks, completed_chunks))
            record.progress = min(99, max(record.progress, int(100 * record.completed_chunks / max(1, record.total_chunks))))
            record.updated_at = datetime.now(timezone.utc)
            return True

    def complete_embedding_job(self, job_id: str, expected_attempt: int) -> bool:
        with self.session_factory.begin() as session:
            record = session.scalar(select(EmbeddingJobRecord).where(
                EmbeddingJobRecord.id == job_id,
                EmbeddingJobRecord.status == "running",
                EmbeddingJobRecord.attempts == expected_attempt,
            ).with_for_update())
            if record is None:
                return False
            record.status, record.progress = "succeeded", 100
            record.completed_chunks = record.total_chunks
            record.error_code = None
            record.updated_at = datetime.now(timezone.utc)
            return True

    def fail_embedding_job(
        self, job_id: str, expected_attempt: int, error_code: str, max_attempts: int,
    ) -> bool:
        safe_code = re.sub(r"[^a-z0-9_]", "_", error_code.lower())[:64] or "embedding_failed"
        with self.session_factory.begin() as session:
            record = session.scalar(select(EmbeddingJobRecord).where(
                EmbeddingJobRecord.id == job_id,
                EmbeddingJobRecord.status == "running",
                EmbeddingJobRecord.attempts == expected_attempt,
            ).with_for_update())
            if record is None:
                return False
            record.status = "failed" if record.attempts >= max_attempts else "queued"
            record.error_code = safe_code
            record.updated_at = datetime.now(timezone.utc)
            return True

    def reap_embedding_jobs(
        self, lease_seconds: int, max_attempts: int, limit: int = 100,
    ) -> list[str]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=lease_seconds)
        result: list[str] = []
        with self.session_factory.begin() as session:
            records = session.scalars(
                select(EmbeddingJobRecord)
                .where(or_(
                    (EmbeddingJobRecord.status == "queued") & or_(
                        EmbeddingJobRecord.error_code.is_(None),
                        EmbeddingJobRecord.error_code != "dispatched",
                        EmbeddingJobRecord.updated_at < cutoff,
                    ),
                    (EmbeddingJobRecord.status == "running") & (EmbeddingJobRecord.updated_at < cutoff),
                ))
                .order_by(EmbeddingJobRecord.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for record in records:
                if record.attempts >= max_attempts:
                    record.status, record.error_code, record.updated_at = "failed", "retry_limit", now
                    continue
                if record.status == "running":
                    record.status, record.error_code, record.updated_at = "queued", "lease_expired", now
                # Reserve delivery for one dispatch interval. If Redis delivery is
                # lost, the lease cutoff makes the DB job dispatchable again.
                record.error_code, record.updated_at = "dispatched", now
                result.append(record.id)
        return result

    def fail_ingestion(self, job_id: str, paper_id: str, message: str, expected_attempt: int) -> bool:
        with self.session_factory.begin() as session:
            job = session.scalar(select(IngestionJobRecord).where(IngestionJobRecord.id == job_id, IngestionJobRecord.paper_id == paper_id).with_for_update())
            paper = session.get(PaperRecord, paper_id)
            if job is None or paper is None: raise PaperNotFoundError(job_id)
            if job.status != "running" or job.attempts != expected_attempt: return False
            job.status, job.error_message, job.updated_at = "failed", message[:2000], datetime.now(timezone.utc)
            paper.status, paper.error_message = "failed", message[:2000]
            return True

    def abort_queued_ingestion(self, job_id: str, paper_id: str, message: str) -> bool:
        """Administrative failure path used before any worker owns an attempt."""
        with self.session_factory.begin() as session:
            job = session.scalar(select(IngestionJobRecord).where(IngestionJobRecord.id == job_id, IngestionJobRecord.paper_id == paper_id).with_for_update())
            paper = session.get(PaperRecord, paper_id)
            if job is None or paper is None: raise PaperNotFoundError(job_id)
            if job.status != "queued": return False
            job.status, job.error_message, job.updated_at = "failed", message[:2000], datetime.now(timezone.utc)
            paper.status, paper.error_message = "failed", message[:2000]
            return True

    def get_page_extraction(self, workspace_id: str, paper_id: str, page: int) -> PaperPage:
        paper = self.get_owned(workspace_id, paper_id)
        with self.session_factory() as session:
            page_record = session.scalar(select(PaperPageRecord).where(PaperPageRecord.paper_id == paper_id, PaperPageRecord.page == page))
            elements = session.scalars(select(DocumentElementRecord).where(DocumentElementRecord.paper_id == paper_id, DocumentElementRecord.page == page).order_by(DocumentElementRecord.id)).all()
        if page_record is None:
            if page <= paper.page_count: return PaperPage(paper_id=paper_id, page=page, chunks=[c for c in paper.chunks if c.page == page])
            raise PaperNotFoundError(str(page))
        return PaperPage(paper_id=paper_id, page=page, chunks=[c for c in paper.chunks if c.page == page], text=page_record.text, text_source=page_record.text_source, quality=page_record.quality, elements=[_element_model(e) for e in elements])

    def list_document_elements(self, workspace_id: str, paper_id: str) -> list[DocumentElement]:
        self.get_owned(workspace_id, paper_id)
        with self.session_factory() as session:
            rows = session.scalars(select(DocumentElementRecord).where(DocumentElementRecord.paper_id == paper_id).order_by(DocumentElementRecord.page, DocumentElementRecord.id)).all()
            return [_element_model(row) for row in rows]

    def get_document_element(self, workspace_id: str, paper_id: str, element_id: str) -> DocumentElement:
        self.get_owned(workspace_id, paper_id)
        with self.session_factory() as session:
            record = session.scalar(select(DocumentElementRecord).where(DocumentElementRecord.paper_id == paper_id, DocumentElementRecord.id == element_id))
            if record is None: raise PaperNotFoundError(element_id)
            return _element_model(record)

    # --- Immutable source provenance and knowledge graph -----------------

    @staticmethod
    def _scoped_node(session: Session, workspace_id: str, node_id: str) -> KnowledgeNodeRecord:
        record = session.scalar(select(KnowledgeNodeRecord).where(
            KnowledgeNodeRecord.workspace_id == workspace_id,
            KnowledgeNodeRecord.id == node_id,
        ))
        if record is None:
            raise PaperNotFoundError(node_id)
        return record

    @staticmethod
    def _require_workspace(session: Session, workspace_id: str) -> None:
        if session.get(WorkspaceRecord, workspace_id) is None:
            raise WorkspaceAccessError(workspace_id)

    @staticmethod
    def _scoped_span(session: Session, workspace_id: str, span_id: str) -> SourceSpanRecord:
        record = session.scalar(select(SourceSpanRecord).where(
            SourceSpanRecord.workspace_id == workspace_id,
            SourceSpanRecord.id == span_id,
        ))
        if record is None:
            raise PaperNotFoundError(span_id)
        return record

    @staticmethod
    def _add_evidence_refs(
        session: Session, workspace_id: str, span_ids: list[str], *,
        knowledge_node_id: str | None = None, knowledge_edge_id: str | None = None,
        excerpt: str = "",
    ) -> list[EvidenceRefRecord]:
        if not span_ids:
            return []
        unique_ids = list(dict.fromkeys(span_ids))
        spans = session.scalars(select(SourceSpanRecord).where(
            SourceSpanRecord.workspace_id == workspace_id,
            SourceSpanRecord.id.in_(unique_ids),
        )).all()
        if len(spans) != len(unique_ids):
            raise PaperNotFoundError("source span")
        now = datetime.now(timezone.utc)
        records = [EvidenceRefRecord(
            id=str(uuid4()), workspace_id=workspace_id, source_span_id=span_id,
            knowledge_node_id=knowledge_node_id, knowledge_edge_id=knowledge_edge_id,
            excerpt=excerpt, created_at=now,
        ) for span_id in unique_ids]
        session.add_all(records)
        return records

    @staticmethod
    def _node_evidence_map(session: Session, node_ids: list[str]) -> dict[str, list[EvidenceRefRecord]]:
        if not node_ids:
            return {}
        rows = session.scalars(select(EvidenceRefRecord).where(
            EvidenceRefRecord.knowledge_node_id.in_(node_ids)
        ).order_by(EvidenceRefRecord.created_at, EvidenceRefRecord.id)).all()
        grouped: dict[str, list[EvidenceRefRecord]] = {node_id: [] for node_id in node_ids}
        for row in rows:
            if row.knowledge_node_id:
                grouped.setdefault(row.knowledge_node_id, []).append(row)
        return grouped

    @staticmethod
    def _edge_evidence_map(session: Session, edge_ids: list[str]) -> dict[str, list[EvidenceRefRecord]]:
        if not edge_ids:
            return {}
        rows = session.scalars(select(EvidenceRefRecord).where(
            EvidenceRefRecord.knowledge_edge_id.in_(edge_ids)
        ).order_by(EvidenceRefRecord.created_at, EvidenceRefRecord.id)).all()
        grouped: dict[str, list[EvidenceRefRecord]] = {edge_id: [] for edge_id in edge_ids}
        for row in rows:
            if row.knowledge_edge_id:
                grouped.setdefault(row.knowledge_edge_id, []).append(row)
        return grouped

    def create_source_version(
        self, workspace_id: str, *, kind: str, locator: str, content_hash: str,
        paper_id: str | None = None, metadata: dict | None = None,
    ) -> SourceVersion:
        """Create or return an immutable content-addressed source version."""
        if not kind.strip() or not locator.strip() or not re.fullmatch(r"[0-9a-fA-F]{64}", content_hash):
            raise ValueError("kind, locator, and a SHA-256 hex content_hash are required")
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            if paper_id is not None:
                paper = session.scalar(select(PaperRecord).where(
                    PaperRecord.id == paper_id, PaperRecord.workspace_id == workspace_id,
                ))
                if paper is None:
                    raise PaperNotFoundError(paper_id)
            existing = session.scalar(select(SourceVersionRecord).where(
                SourceVersionRecord.workspace_id == workspace_id,
                SourceVersionRecord.kind == kind.strip()[:32],
                SourceVersionRecord.locator == locator.strip(),
                SourceVersionRecord.content_hash == content_hash,
            ))
            if existing is not None:
                return _source_version_model(existing)
            record = SourceVersionRecord(
                id=str(uuid4()), workspace_id=workspace_id, paper_id=paper_id,
                kind=kind.strip()[:32], locator=locator.strip(), content_hash=content_hash,
                metadata_json=dict(metadata or {}), created_at=datetime.now(timezone.utc),
            )
            session.add(record)
            session.flush()
            return _source_version_model(record)

    def create_source_import(
        self, workspace_id: str, *, kind: str, locator: str, content_hash: str,
        metadata: dict | None, spans: list[dict],
    ) -> tuple[SourceVersion, list[SourceSpan]]:
        """Atomically create one parsed immutable source and all of its spans.

        The idempotency key includes the semantic source identity, not just the
        bytes.  The same text may legitimately be imported from different
        locators or parsed under different formats.
        """
        if not kind.strip() or not locator.strip() or not re.fullmatch(r"[0-9a-fA-F]{64}", content_hash):
            raise ValueError("kind, locator, and a SHA-256 hex content_hash are required")
        normalized_kind = kind.strip()[:32]
        normalized_locator = locator.strip()
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            existing = session.scalar(select(SourceVersionRecord).where(
                SourceVersionRecord.workspace_id == workspace_id,
                SourceVersionRecord.kind == normalized_kind,
                SourceVersionRecord.locator == normalized_locator,
                SourceVersionRecord.content_hash == content_hash,
            ))
            if existing is not None:
                existing_spans = session.scalars(select(SourceSpanRecord).where(
                    SourceSpanRecord.workspace_id == workspace_id,
                    SourceSpanRecord.source_version_id == existing.id,
                ).order_by(SourceSpanRecord.page, SourceSpanRecord.created_at, SourceSpanRecord.id)).all()
                if len(existing_spans) != len(spans):
                    raise ResourceConflictError("source version already exists with a different immutable span set")
                return _source_version_model(existing), [_source_span_model(row) for row in existing_spans]

            source = SourceVersionRecord(
                id=str(uuid4()), workspace_id=workspace_id, paper_id=None,
                kind=normalized_kind, locator=normalized_locator, content_hash=content_hash,
                metadata_json=dict(metadata or {}), created_at=datetime.now(timezone.utc),
            )
            session.add(source)
            records: list[SourceSpanRecord] = []
            for item in spans:
                line_start, line_end = item.get("line_start"), item.get("line_end")
                char_start, char_end = item.get("char_start"), item.get("char_end")
                if line_start is not None and line_end is not None and line_end < line_start:
                    raise ValueError("line_end must be greater than or equal to line_start")
                if char_start is not None and char_end is not None and char_end < char_start:
                    raise ValueError("char_end must be greater than or equal to char_start")
                records.append(SourceSpanRecord(
                    id=str(uuid4()), workspace_id=workspace_id, source_version_id=source.id,
                    page=item.get("page"), line_start=line_start, line_end=line_end,
                    char_start=char_start, char_end=char_end, bbox=item.get("bbox"),
                    cell=item.get("cell"), locator_json=dict(item.get("locator") or {}),
                    text=str(item.get("text") or ""), created_at=datetime.now(timezone.utc),
                ))
            session.add_all(records)
            session.flush()
            return _source_version_model(source), [_source_span_model(row) for row in records]

    def get_source_version(self, workspace_id: str, source_version_id: str) -> SourceVersion:
        with self.session_factory() as session:
            record = session.scalar(select(SourceVersionRecord).where(
                SourceVersionRecord.workspace_id == workspace_id,
                SourceVersionRecord.id == source_version_id,
            ))
            if record is None:
                raise PaperNotFoundError(source_version_id)
            return _source_version_model(record)

    def list_source_versions(self, workspace_id: str, kind: str | None = None) -> list[SourceVersion]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            statement = select(SourceVersionRecord).where(SourceVersionRecord.workspace_id == workspace_id)
            if kind:
                statement = statement.where(SourceVersionRecord.kind == kind)
            rows = session.scalars(statement.order_by(SourceVersionRecord.created_at.desc())).all()
            return [_source_version_model(row) for row in rows]

    def create_source_span(
        self, workspace_id: str, source_version_id: str, *, page: int | None = None,
        line_start: int | None = None, line_end: int | None = None,
        char_start: int | None = None, char_end: int | None = None,
        bbox: list[float] | None = None, cell: dict | list | None = None,
        locator: dict | None = None, text_value: str = "",
    ) -> SourceSpan:
        if line_start is not None and line_end is not None and line_end < line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        if char_start is not None and char_end is not None and char_end < char_start:
            raise ValueError("char_end must be greater than or equal to char_start")
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            version = session.scalar(select(SourceVersionRecord).where(
                SourceVersionRecord.workspace_id == workspace_id,
                SourceVersionRecord.id == source_version_id,
            ))
            if version is None:
                raise PaperNotFoundError(source_version_id)
            record = SourceSpanRecord(
                id=str(uuid4()), workspace_id=workspace_id, source_version_id=source_version_id,
                page=page, line_start=line_start, line_end=line_end,
                char_start=char_start, char_end=char_end, bbox=bbox, cell=cell,
                locator_json=dict(locator or {}), text=text_value,
                created_at=datetime.now(timezone.utc),
            )
            session.add(record)
            session.flush()
            return _source_span_model(record)

    def get_source_span(self, workspace_id: str, source_span_id: str) -> SourceSpan:
        with self.session_factory() as session:
            return _source_span_model(self._scoped_span(session, workspace_id, source_span_id))

    def list_source_spans(self, workspace_id: str, source_version_id: str) -> list[SourceSpan]:
        with self.session_factory() as session:
            version = session.scalar(select(SourceVersionRecord.id).where(
                SourceVersionRecord.workspace_id == workspace_id,
                SourceVersionRecord.id == source_version_id,
            ))
            if version is None:
                raise PaperNotFoundError(source_version_id)
            rows = session.scalars(select(SourceSpanRecord).where(
                SourceSpanRecord.workspace_id == workspace_id,
                SourceSpanRecord.source_version_id == source_version_id,
            ).order_by(SourceSpanRecord.page, SourceSpanRecord.created_at, SourceSpanRecord.id)).all()
            return [_source_span_model(row) for row in rows]

    def create_knowledge_node(
        self, workspace_id: str, *, node_type: str, content: str, layer: int = 0,
        status: str = "review_pending", phase: str = "unclassified", confidence: float | None = None,
        created_by: str | None = None, metadata: dict | None = None,
        evidence_span_ids: list[str] | None = None, evidence_excerpt: str = "",
    ) -> KnowledgeNode:
        status = {"draft": "review_pending", "validated": "verified"}.get(status, status)
        evidence_span_ids = evidence_span_ids or []
        if node_type == "source" and not evidence_span_ids:
            raise ValueError("source knowledge nodes require at least one source span")
        if not content.strip():
            raise ValueError("knowledge node content is required")
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            record = KnowledgeNodeRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                node_type=node_type, status=status, layer=layer, content=content.strip(),
                phase=phase.strip() or "unclassified", confidence=confidence,
                metadata_json=dict(metadata or {}), created_at=now, updated_at=now,
            )
            session.add(record)
            session.flush()
            evidence = self._add_evidence_refs(
                session, workspace_id, evidence_span_ids, knowledge_node_id=record.id,
                excerpt=evidence_excerpt,
            )
            return _knowledge_node_model(record, evidence)

    def get_knowledge_node(self, workspace_id: str, node_id: str) -> KnowledgeNode:
        with self.session_factory() as session:
            record = self._scoped_node(session, workspace_id, node_id)
            evidence = self._node_evidence_map(session, [node_id]).get(node_id, [])
            return _knowledge_node_model(record, evidence)

    def list_knowledge_nodes(
        self, workspace_id: str, *, status: str | None = None, layer: int | None = None,
    ) -> list[KnowledgeNode]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            statement = select(KnowledgeNodeRecord).where(KnowledgeNodeRecord.workspace_id == workspace_id)
            if status:
                statement = statement.where(KnowledgeNodeRecord.status == status)
            if layer is not None:
                statement = statement.where(KnowledgeNodeRecord.layer == layer)
            rows = session.scalars(statement.order_by(KnowledgeNodeRecord.layer, KnowledgeNodeRecord.created_at, KnowledgeNodeRecord.id)).all()
            evidence = self._node_evidence_map(session, [row.id for row in rows])
            return [_knowledge_node_model(row, evidence.get(row.id, [])) for row in rows]

    def add_node_evidence(
        self, workspace_id: str, node_id: str, source_span_ids: list[str], *, excerpt: str = "",
    ) -> list[EvidenceRef]:
        with self.session_factory.begin() as session:
            self._scoped_node(session, workspace_id, node_id)
            refs = self._add_evidence_refs(
                session, workspace_id, source_span_ids, knowledge_node_id=node_id, excerpt=excerpt,
            )
            return [_evidence_ref_model(record) for record in refs]

    def update_knowledge_node(
        self, workspace_id: str, node_id: str, *, content: str | None = None,
        phase: str | None = None, confidence: float | None = None,
        metadata: dict | None = None,
    ) -> KnowledgeNode:
        with self.session_factory.begin() as session:
            record = self._scoped_node(session, workspace_id, node_id)
            if content is not None:
                if not content.strip():
                    raise ValueError("knowledge node content is required")
                record.content = content.strip()
            if phase is not None:
                record.phase = phase.strip() or "unclassified"
            if confidence is not None:
                record.confidence = confidence
            if metadata is not None:
                record.metadata_json = dict(metadata)
            record.updated_at = datetime.now(timezone.utc)
            evidence = self._node_evidence_map(session, [node_id]).get(node_id, [])
            return _knowledge_node_model(record, evidence)

    def propagate_downstream_review_required(
        self, workspace_id: str, node_id: str,
    ) -> list[str]:
        """Mark all non-terminal descendants as needing review after a bad premise."""
        with self.session_factory.begin() as session:
            self._scoped_node(session, workspace_id, node_id)
            visited = {node_id}
            frontier = {node_id}
            descendants: list[str] = []
            while frontier:
                targets = session.scalars(select(KnowledgeEdgeRecord.target_node_id).where(
                    KnowledgeEdgeRecord.workspace_id == workspace_id,
                    KnowledgeEdgeRecord.source_node_id.in_(frontier),
                )).all()
                frontier = {target for target in targets if target not in visited}
                visited.update(frontier)
                descendants.extend(sorted(frontier))
            if descendants:
                records = session.scalars(select(KnowledgeNodeRecord).where(
                    KnowledgeNodeRecord.workspace_id == workspace_id,
                    KnowledgeNodeRecord.id.in_(descendants),
                )).all()
                now = datetime.now(timezone.utc)
                for record in records:
                    if record.status not in {"rejected", "superseded", "pruned"}:
                        record.status, record.updated_at = "review_required", now
            return descendants

    def set_knowledge_node_status(
        self, workspace_id: str, node_id: str, status: str,
    ) -> tuple[KnowledgeNode, list[str]]:
        """Set status and cascade review_required for rejected/superseded premises."""
        status = {"draft": "review_pending", "validated": "verified"}.get(status, status)
        if status not in {"review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"}:
            raise ValueError("invalid knowledge node status")
        with self.session_factory.begin() as session:
            record = self._scoped_node(session, workspace_id, node_id)
            record.status, record.updated_at = status, datetime.now(timezone.utc)
            affected: list[str] = []
            if status in {"rejected", "superseded"}:
                visited = {node_id}
                frontier = {node_id}
                while frontier:
                    targets = session.scalars(select(KnowledgeEdgeRecord.target_node_id).where(
                        KnowledgeEdgeRecord.workspace_id == workspace_id,
                        KnowledgeEdgeRecord.source_node_id.in_(frontier),
                    )).all()
                    frontier = {target for target in targets if target not in visited}
                    visited.update(frontier)
                    affected.extend(sorted(frontier))
                if affected:
                    descendants = session.scalars(select(KnowledgeNodeRecord).where(
                        KnowledgeNodeRecord.workspace_id == workspace_id,
                        KnowledgeNodeRecord.id.in_(affected),
                    )).all()
                    now = datetime.now(timezone.utc)
                    for descendant in descendants:
                        if descendant.status not in {"rejected", "superseded", "pruned"}:
                            descendant.status, descendant.updated_at = "review_required", now
            evidence = self._node_evidence_map(session, [node_id]).get(node_id, [])
            model = _knowledge_node_model(record, evidence)
            return model, affected

    def create_knowledge_edge(
        self, workspace_id: str, *, source_node_id: str, target_node_id: str,
        relation: str, evidence_span_ids: list[str], metadata: dict | None = None,
        evidence_excerpt: str = "",
    ) -> KnowledgeEdge:
        if source_node_id == target_node_id:
            raise ValueError("knowledge edges must connect distinct nodes")
        if not relation.strip() or not evidence_span_ids:
            raise ValueError("knowledge edges require a relation and at least one evidence span")
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            self._scoped_node(session, workspace_id, source_node_id)
            self._scoped_node(session, workspace_id, target_node_id)
            record = KnowledgeEdgeRecord(
                id=str(uuid4()), workspace_id=workspace_id,
                source_node_id=source_node_id, target_node_id=target_node_id,
                relation=relation.strip()[:64], metadata_json=dict(metadata or {}),
                created_at=datetime.now(timezone.utc),
            )
            session.add(record)
            session.flush()
            evidence = self._add_evidence_refs(
                session, workspace_id, evidence_span_ids, knowledge_edge_id=record.id,
                excerpt=evidence_excerpt,
            )
            return _knowledge_edge_model(record, evidence)

    def list_knowledge_edges(self, workspace_id: str, node_id: str | None = None) -> list[KnowledgeEdge]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            statement = select(KnowledgeEdgeRecord).where(KnowledgeEdgeRecord.workspace_id == workspace_id)
            if node_id:
                self._scoped_node(session, workspace_id, node_id)
                statement = statement.where(or_(
                    KnowledgeEdgeRecord.source_node_id == node_id,
                    KnowledgeEdgeRecord.target_node_id == node_id,
                ))
            rows = session.scalars(statement.order_by(KnowledgeEdgeRecord.created_at, KnowledgeEdgeRecord.id)).all()
            evidence = self._edge_evidence_map(session, [row.id for row in rows])
            return [_knowledge_edge_model(row, evidence.get(row.id, [])) for row in rows]

    def create_reasoning_run(
        self, workspace_id: str, *, operator: str, input_node_ids: list[str] | None = None,
        output_node_ids: list[str] | None = None, status: str = "queued", prompt: str = "",
        created_by: str | None = None, metadata: dict | None = None,
    ) -> ReasoningRun:
        if not operator.strip():
            raise ValueError("reasoning operator is required")
        input_node_ids, output_node_ids = input_node_ids or [], output_node_ids or []
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            node_ids = list(dict.fromkeys(input_node_ids + output_node_ids))
            if node_ids:
                rows = session.scalars(select(KnowledgeNodeRecord.id).where(
                    KnowledgeNodeRecord.workspace_id == workspace_id,
                    KnowledgeNodeRecord.id.in_(node_ids),
                )).all()
                if len(rows) != len(node_ids):
                    raise PaperNotFoundError("knowledge node")
            record = ReasoningRunRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                operator=operator.strip()[:64], status=status, prompt=prompt,
                metadata_json=dict(metadata or {}), created_at=now, updated_at=now,
            )
            session.add(record)
            inputs = [ReasoningRunInputRecord(
                id=str(uuid4()), reasoning_run_id=record.id, knowledge_node_id=node_id, ordinal=index,
            ) for index, node_id in enumerate(dict.fromkeys(input_node_ids), start=1)]
            outputs = [ReasoningRunOutputRecord(
                id=str(uuid4()), reasoning_run_id=record.id, knowledge_node_id=node_id, ordinal=index,
            ) for index, node_id in enumerate(dict.fromkeys(output_node_ids), start=1)]
            session.add_all(inputs + outputs)
            session.flush()
            return _reasoning_run_model(record, inputs, outputs)

    def get_reasoning_run(self, workspace_id: str, reasoning_run_id: str) -> ReasoningRun:
        with self.session_factory() as session:
            record = session.scalar(select(ReasoningRunRecord).where(
                ReasoningRunRecord.workspace_id == workspace_id,
                ReasoningRunRecord.id == reasoning_run_id,
            ))
            if record is None:
                raise PaperNotFoundError(reasoning_run_id)
            inputs = session.scalars(select(ReasoningRunInputRecord).where(
                ReasoningRunInputRecord.reasoning_run_id == reasoning_run_id,
            ).order_by(ReasoningRunInputRecord.ordinal)).all()
            outputs = session.scalars(select(ReasoningRunOutputRecord).where(
                ReasoningRunOutputRecord.reasoning_run_id == reasoning_run_id,
            ).order_by(ReasoningRunOutputRecord.ordinal)).all()
            return _reasoning_run_model(record, inputs, outputs)

    def update_reasoning_run(
        self, workspace_id: str, reasoning_run_id: str, *, status: str | None = None,
        output_node_ids: list[str] | None = None, metadata: dict | None = None,
    ) -> ReasoningRun:
        with self.session_factory.begin() as session:
            record = session.scalar(select(ReasoningRunRecord).where(
                ReasoningRunRecord.workspace_id == workspace_id,
                ReasoningRunRecord.id == reasoning_run_id,
            ))
            if record is None:
                raise PaperNotFoundError(reasoning_run_id)
            if status is not None:
                if status not in {"queued", "running", "succeeded", "failed", "cancelled"}:
                    raise ValueError("invalid reasoning run status")
                record.status = status
            if metadata is not None:
                record.metadata_json = dict(metadata)
            if output_node_ids is not None:
                unique_ids = list(dict.fromkeys(output_node_ids))
                if unique_ids:
                    owned = session.scalars(select(KnowledgeNodeRecord.id).where(
                        KnowledgeNodeRecord.workspace_id == workspace_id,
                        KnowledgeNodeRecord.id.in_(unique_ids),
                    )).all()
                    if len(owned) != len(unique_ids):
                        raise PaperNotFoundError("knowledge node")
                session.execute(delete(ReasoningRunOutputRecord).where(
                    ReasoningRunOutputRecord.reasoning_run_id == reasoning_run_id,
                ))
                session.add_all([ReasoningRunOutputRecord(
                    id=str(uuid4()), reasoning_run_id=reasoning_run_id,
                    knowledge_node_id=node_id, ordinal=index,
                ) for index, node_id in enumerate(unique_ids, start=1)])
            record.updated_at = datetime.now(timezone.utc)
            inputs = session.scalars(select(ReasoningRunInputRecord).where(
                ReasoningRunInputRecord.reasoning_run_id == reasoning_run_id,
            ).order_by(ReasoningRunInputRecord.ordinal)).all()
            outputs = session.scalars(select(ReasoningRunOutputRecord).where(
                ReasoningRunOutputRecord.reasoning_run_id == reasoning_run_id,
            ).order_by(ReasoningRunOutputRecord.ordinal)).all()
            return _reasoning_run_model(record, inputs, outputs)

    def upsert_node_feedback(
        self, workspace_id: str, node_id: str, user_id: str, *, verdict: str,
        rating: float | None = None, comment: str = "",
    ) -> NodeFeedback:
        if verdict not in {"helpful", "not_helpful", "accepted", "rejected"}:
            raise ValueError("invalid node feedback verdict")
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            self._scoped_node(session, workspace_id, node_id)
            record = session.scalar(select(NodeFeedbackRecord).where(
                NodeFeedbackRecord.knowledge_node_id == node_id,
                NodeFeedbackRecord.user_id == user_id,
            ))
            if record is None:
                record = NodeFeedbackRecord(
                    id=str(uuid4()), workspace_id=workspace_id, knowledge_node_id=node_id,
                    user_id=user_id, verdict=verdict, rating=rating, comment=comment,
                    created_at=now, updated_at=now,
                )
                session.add(record)
            else:
                record.verdict, record.rating, record.comment, record.updated_at = verdict, rating, comment, now
            session.flush()
            return _node_feedback_model(record)

    def list_node_feedback(self, workspace_id: str, node_id: str) -> list[NodeFeedback]:
        with self.session_factory() as session:
            self._scoped_node(session, workspace_id, node_id)
            rows = session.scalars(select(NodeFeedbackRecord).where(
                NodeFeedbackRecord.workspace_id == workspace_id,
                NodeFeedbackRecord.knowledge_node_id == node_id,
            ).order_by(NodeFeedbackRecord.updated_at.desc())).all()
            return [_node_feedback_model(row) for row in rows]

    def upsert_canvas_layout(
        self, workspace_id: str, node_id: str, *, x: float, y: float,
        canvas_id: str = "default", width: float | None = None,
        height: float | None = None, z_index: int = 0, collapsed: bool = False,
    ) -> CanvasLayout:
        if not canvas_id.strip():
            raise ValueError("canvas_id is required")
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            self._scoped_node(session, workspace_id, node_id)
            record = session.scalar(select(CanvasLayoutRecord).where(
                CanvasLayoutRecord.workspace_id == workspace_id,
                CanvasLayoutRecord.canvas_id == canvas_id,
                CanvasLayoutRecord.knowledge_node_id == node_id,
            ).with_for_update())
            now = datetime.now(timezone.utc)
            if record is None:
                record = CanvasLayoutRecord(
                    id=str(uuid4()), workspace_id=workspace_id, canvas_id=canvas_id,
                    knowledge_node_id=node_id, x=x, y=y, width=width, height=height,
                    z_index=z_index, collapsed=collapsed, updated_at=now,
                )
                session.add(record)
            else:
                record.x, record.y, record.width, record.height = x, y, width, height
                record.z_index, record.collapsed, record.updated_at = z_index, collapsed, now
            session.flush()
            return _canvas_layout_model(record)

    def list_canvas_layouts(self, workspace_id: str, canvas_id: str = "default") -> list[CanvasLayout]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            rows = session.scalars(select(CanvasLayoutRecord).where(
                CanvasLayoutRecord.workspace_id == workspace_id,
                CanvasLayoutRecord.canvas_id == canvas_id,
            ).order_by(CanvasLayoutRecord.z_index, CanvasLayoutRecord.updated_at, CanvasLayoutRecord.id)).all()
            return [_canvas_layout_model(row) for row in rows]
