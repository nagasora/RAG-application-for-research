from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .database import (
    Base, ChunkRecord, DocumentElementRecord, IngestionJobRecord, NoteRecord, PaperPageRecord,
    PaperRecord, PaperTagRecord, SavedComparisonRecord, SearchHistoryRecord, TagRecord,
    UserRecord, WorkspaceMemberRecord, WorkspaceRecord,
    create_database_engine, create_session_factory,
)
from .models import Chunk, DocumentElement, IngestionJob, Note, Paper, PaperPage, Principal, SavedComparison, SearchHistory, Tag, User, Workspace


class DuplicatePaperError(Exception):
    def __init__(self, paper: Paper):
        super().__init__(f"duplicate paper: {paper.id}")
        self.paper = paper


class PaperNotFoundError(Exception):
    pass


class WorkspaceAccessError(Exception):
    pass


class ResourceConflictError(Exception):
    pass


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
                session.add(personal)
                session.add(membership)
        except IntegrityError:
            # A concurrent first request may have provisioned the same OIDC subject.
            return self.ensure_user(principal)
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
            session.add(membership)
        return _workspace_model(record, "owner")

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

    def mark_ready(self, paper: Paper) -> Paper:
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

    def upsert(self, paper: Paper) -> Paper:
        """Compatibility path for external imports; insert atomically and deduplicate by hash."""
        existing = self.get_by_hash(paper.workspace_id, paper.content_hash or "") if paper.content_hash else None
        if existing:
            raise DuplicatePaperError(existing)
        paper.status = "ready"
        try:
            with self.session_factory.begin() as session:
                session.add(_record_from_model(paper))
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

    def complete_ingestion(self, job_id: str, paper: Paper, pages: list[PaperPage], elements: list[DocumentElement], expected_attempt: int) -> None:
        with self.session_factory.begin() as session:
            job = session.scalar(select(IngestionJobRecord).where(IngestionJobRecord.id == job_id, IngestionJobRecord.paper_id == paper.id).with_for_update())
            record = session.scalar(select(PaperRecord).where(PaperRecord.id == paper.id).with_for_update())
            if job is None or record is None: raise PaperNotFoundError(job_id)
            if job.status != "running" or job.attempts != expected_attempt: raise ResourceConflictError("ingestion lease was superseded")
            session.execute(delete(ChunkRecord).where(ChunkRecord.paper_id == paper.id)); session.execute(delete(PaperPageRecord).where(PaperPageRecord.paper_id == paper.id)); session.execute(delete(DocumentElementRecord).where(DocumentElementRecord.paper_id == paper.id))
            session.add_all([ChunkRecord(id=c.id, paper_id=paper.id, page=c.page, section=c.section, text=c.text) for c in paper.chunks])
            session.add_all([PaperPageRecord(paper_id=paper.id, page=p.page, text=p.text, text_source=p.text_source, quality=p.quality) for p in pages])
            session.add_all([DocumentElementRecord(id=e.id, paper_id=paper.id, page=e.page, kind=e.kind, bbox=e.bbox, text=e.text, structured_data=e.structured_data, asset_key=e.asset_key) for e in elements])
            record.title, record.abstract, record.page_count, record.status, record.error_message = paper.title, paper.abstract, paper.page_count, "ready", None
            job.status, job.progress, job.error_message, job.updated_at = "succeeded", 100, None, datetime.now(timezone.utc)

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
