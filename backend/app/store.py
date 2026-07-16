from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import os
import re
from uuid import uuid4

from sqlalchemy import and_, case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .database import (
    Base, BeliefEventRecord, CanvasLayoutRecord, ChunkEmbeddingRecord, ChunkRecord, DocumentElementRecord, ExperimentPlanRecord, HypothesisCardRecord, DiscoveryItemRecord, IdeaRecord,
    EmbeddingJobRecord, EvidenceRefRecord, IngestionJobRecord, KnowledgeEdgeRecord, KnowledgeEdgeStatusEventRecord,
    KnowledgeNodeRecord, NodeFeedbackRecord, NoteRecord, PaperDecisionRecord, PaperPageRecord, PaperRecord,
    PaperTagRecord, ReasoningRunInputRecord, ReasoningRunOutputRecord, ReasoningRunRecord,
    ResearchConversationRecord, ResearchMemoryEventRecord, ResearchMessageRecord,
    ResearchQuestionRecord, ResearchRunRecord, ReviewCommentRecord, ReviewDecisionRecord, ReviewThreadRecord, RunArtifactRecord, SourceSetPaperRecord, SourceSetRecord,
    SavedComparisonRecord, SearchHistoryRecord, SourceSpanRecord, SourceVersionRecord,
    TagRecord, UserRecord, WorkspaceMemberRecord, WorkspaceRecord,
    create_database_engine, create_session_factory,
)
from .models import (
    BeliefEvent, BeliefEventCreate, CanvasLayout, Chunk, Citation, DiscoveryItem, DiscoveryItemCreate, DocumentElement, EvidenceLinkCreate, EvidenceRef, ExperimentPlan, ExperimentPlanCreate, ExperimentPlanSnapshot, HypothesisCard, HypothesisCardCreate, IngestionJob,
    ForwardPropagationResult, KnowledgeEdge, KnowledgeNode, NodeFeedback, Note, Paper, PaperDecision, PaperLibraryFacets, PaperLibraryItem, PaperLibraryPage, PaperPage, Principal,
    ReasoningRun, ReasoningRunLink, ResearchConversation, ResearchConversationDetail,
    ResearchMemoryEvent, ResearchMemoryPage, ResearchMessage, ResearchMessagePage,
    Idea, IdeaCreate, IdeaUpdate, ResearchQuestion, ResearchRun, ReviewComment, ReviewDecision, ReviewThread, ReviewThreadCreate, RunArtifact, SourceSet,
    SavedComparison, SearchHistory, SourceSpan, SourceVersion, Tag, User, Workspace,
    WorkspaceMember,
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


class WorkspaceMemberNotFoundError(Exception):
    pass


class WorkspaceMemberConflictError(Exception):
    pass


class ResourceConflictError(Exception):
    pass


_FORWARD_DEPENDENCY_RELATIONS = {"informs", "supports", "formulates"}
_REVERSE_DEPENDENCY_RELATIONS = {"extends", "depends_on", "implements"}
_KNOWLEDGE_RELATIONS = _FORWARD_DEPENDENCY_RELATIONS | _REVERSE_DEPENDENCY_RELATIONS | {"contradicts", "related"}
_KNOWLEDGE_STATUSES = {"review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"}


def _source_span_set_hash(spans: list[dict]) -> str:
    """Hash the complete immutable span set independent of database row order."""
    normalized: list[str] = []
    for item in spans:
        payload = {
            "page": item.get("page"), "line_start": item.get("line_start"),
            "line_end": item.get("line_end"), "char_start": item.get("char_start"),
            "char_end": item.get("char_end"), "bbox": item.get("bbox"),
            "cell": item.get("cell"), "locator": dict(item.get("locator") or {}),
            "text": str(item.get("text") or ""),
        }
        normalized.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    normalized.sort()
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()


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


def _research_question_model(record: ResearchQuestionRecord) -> ResearchQuestion:
    return ResearchQuestion(
        id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
        title=record.title, question=record.question,
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
    )


def _source_set_model(record: SourceSetRecord, paper_ids: list[str]) -> SourceSet:
    return SourceSet(
        id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
        name=record.name, description=record.description, paper_ids=paper_ids,
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
    )


def _run_artifact_model(record: RunArtifactRecord) -> RunArtifact:
    return RunArtifact(
        id=record.id, research_run_id=record.research_run_id, kind=record.kind,
        payload=record.payload, ordinal=record.ordinal, created_at=record.created_at.isoformat(),
    )


def _research_run_model(record: ResearchRunRecord, artifacts: list[RunArtifactRecord]) -> ResearchRun:
    return ResearchRun(
        id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
        research_question_id=record.research_question_id, source_set_id=record.source_set_id,
        research_question=record.research_question, source_paper_ids=list(record.source_paper_ids or []),
        excluded_paper_ids=list(record.excluded_paper_ids or []), purpose=record.purpose,
        success_criteria=record.success_criteria, plan=record.plan, model=record.model,
        prompt_version=record.prompt_version, status=record.status, cancel_requested=record.cancel_requested,
        started_at=record.started_at.isoformat() if record.started_at else None,
        completed_at=record.completed_at.isoformat() if record.completed_at else None,
        created_at=record.created_at.isoformat(), artifacts=[_run_artifact_model(item) for item in artifacts],
    )


def _workspace_member_model(record: WorkspaceMemberRecord, user: UserRecord) -> WorkspaceMember:
    return WorkspaceMember(
        user=_user_model(user), role=record.role, created_at=record.created_at.isoformat(),
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
        knowledge_edge_id=record.knowledge_edge_id,
        source_version_id=record.source_version_id, target_claim=record.target_claim,
        role=record.role, extraction_quality=record.extraction_quality,
        quote_start=record.quote_start, quote_end=record.quote_end,
        verbatim_quote=record.verbatim_quote, excerpt=record.excerpt,
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
        id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
        source_node_id=record.source_node_id, target_node_id=record.target_node_id,
        relation=record.relation, status=record.status, origin=record.origin,
        metadata=dict(record.metadata_json or {}),
        evidence=[_evidence_ref_model(item) for item in evidence or []],
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
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


def _idea_model(record: IdeaRecord) -> Idea:
    return Idea(
        id=record.id, workspace_id=record.workspace_id, kind=record.kind,
        content=record.content, research_run_id=record.research_run_id,
        claim_id=record.claim_id, paper_id=record.paper_id,
        source_span_id=record.source_span_id, checklist=dict(record.checklist or {}),
        status=record.status, hypothesis_card_id=record.hypothesis_card_id,
        created_at=record.created_at.isoformat(),
    )


def _experiment_plan_model(record: ExperimentPlanRecord) -> ExperimentPlan:
    payload = dict(record.plan or {})
    payload["hypothesis_card_id"] = record.hypothesis_card_id
    return ExperimentPlan(
        id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
        results=list(record.results or []), history=list(record.history or []),
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
        **payload,
    )


def _markdown_literal(value) -> str:
    """Render one untrusted value without allowing Markdown/HTML structure injection."""
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return f"<code>{html.escape(serialized, quote=True)}</code>"


def _markdown_block(value: str) -> str:
    return f"<pre>{html.escape(value, quote=True)}</pre>"


class PaperStore:
    def create_experiment_plan(self, workspace_id: str, created_by: str, body: ExperimentPlanCreate) -> ExperimentPlan:
        now = datetime.now(timezone.utc)
        payload = body.model_dump()
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            if body.hypothesis_card_id:
                hypothesis = session.scalar(select(HypothesisCardRecord).where(
                    HypothesisCardRecord.id == body.hypothesis_card_id,
                    HypothesisCardRecord.workspace_id == workspace_id,
                ))
                if hypothesis is None:
                    raise PaperNotFoundError(body.hypothesis_card_id)
                payload["hypothesis_snapshot"] = {
                    "id": hypothesis.id, "claim": hypothesis.claim,
                    "status": hypothesis.status, "mechanism": hypothesis.mechanism,
                    "conditions": hypothesis.conditions,
                    "falsifiers": list(hypothesis.falsifiers or []),
                    "metadata": dict(hypothesis.metadata_json or {}),
                    "captured_at": now.isoformat(),
                }
            row = ExperimentPlanRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                hypothesis_card_id=body.hypothesis_card_id, plan=payload, results=[],
                history=[{"event_id": str(uuid4()), "at": now.isoformat(), "action": "created"}],
                created_at=now, updated_at=now,
            )
            session.add(row)
            session.flush()
            return _experiment_plan_model(row)

    def list_experiment_plans(self, workspace_id: str) -> list[ExperimentPlan]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            rows = session.scalars(select(ExperimentPlanRecord).where(
                ExperimentPlanRecord.workspace_id == workspace_id,
            ).order_by(ExperimentPlanRecord.updated_at.desc(), ExperimentPlanRecord.id)).all()
            return [_experiment_plan_model(row) for row in rows]

    def get_experiment_plan(self, workspace_id: str, plan_id: str) -> ExperimentPlan:
        with self.session_factory() as session:
            row=session.scalar(select(ExperimentPlanRecord).where(ExperimentPlanRecord.workspace_id==workspace_id,ExperimentPlanRecord.id==plan_id))
            if row is None: raise PaperNotFoundError(plan_id)
            return _experiment_plan_model(row)

    def add_experiment_result(self, workspace_id: str, plan_id: str, result: dict) -> ExperimentPlan:
        with self.session_factory.begin() as session:
            row=session.scalar(select(ExperimentPlanRecord).where(ExperimentPlanRecord.workspace_id==workspace_id,ExperimentPlanRecord.id==plan_id).with_for_update())
            if row is None: raise PaperNotFoundError(plan_id)
            now = datetime.now(timezone.utc)
            result_id = str(uuid4())
            result_event = {"id": result_id, "recorded_at": now.isoformat(), **dict(result)}
            history_event = {
                "event_id": str(uuid4()), "at": now.isoformat(),
                "action": "result_recorded", "result_id": result_id,
            }
            row.results = [*list(row.results or []), result_event]
            row.history = [*list(row.history or []), history_event]
            row.updated_at = now
            session.flush()
            return _experiment_plan_model(row)

    def export_experiment_plan_snapshot(self, workspace_id: str, plan_id: str) -> ExperimentPlanSnapshot:
        plan = self.get_experiment_plan(workspace_id, plan_id)
        return ExperimentPlanSnapshot(
            exported_at=datetime.now(timezone.utc).isoformat(), experiment=plan,
        )
    @staticmethod
    def _belief_event_model(record: BeliefEventRecord) -> BeliefEvent:
        return BeliefEvent(id=record.id, workspace_id=record.workspace_id, created_by=record.created_by, belief_key=record.belief_key, content=record.content, status=record.status, reason=record.reason, hypothesis_card_id=record.hypothesis_card_id, reasoning_run_id=record.reasoning_run_id, created_at=record.created_at.isoformat())

    def append_belief_event(self, workspace_id: str, created_by: str, body: BeliefEventCreate) -> BeliefEvent:
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            record = BeliefEventRecord(id=str(uuid4()), workspace_id=workspace_id, created_by=created_by, belief_key=body.belief_key, content=body.content, status=body.status, reason=body.reason, hypothesis_card_id=body.hypothesis_card_id, reasoning_run_id=body.reasoning_run_id, created_at=datetime.now(timezone.utc))
            session.add(record); session.flush(); return self._belief_event_model(record)

    def search_positive_beliefs(self, workspace_id: str, query: str, limit: int = 20) -> list[BeliefEvent]:
        with self.session_factory() as session:
            # A later rejected event dominates prior positive events with the same key.
            rejected = set(session.scalars(select(BeliefEventRecord.belief_key).where(BeliefEventRecord.workspace_id == workspace_id, BeliefEventRecord.status == "rejected")).all())
            rows = session.scalars(select(BeliefEventRecord).where(BeliefEventRecord.workspace_id == workspace_id, BeliefEventRecord.status.in_(("proposed", "supported", "disputed"))).order_by(BeliefEventRecord.created_at.desc())).all()
            terms = [term.casefold() for term in query.split() if term]
            return [self._belief_event_model(row) for row in rows if row.belief_key not in rejected and (not terms or any(term in row.content.casefold() for term in terms))][:limit]
    @staticmethod
    def _discovery_item_model(record: DiscoveryItemRecord) -> DiscoveryItem:
        return DiscoveryItem(id=record.id, workspace_id=record.workspace_id, created_by=record.created_by, provider=record.provider, provider_paper_id=record.provider_paper_id, classification=record.classification, review_status=record.review_status, title=record.title, abstract=record.abstract, source_quote=record.source_quote, source_url=record.source_url, license=record.license, rate_limit_policy=record.rate_limit_policy, snapshot=dict(record.snapshot or {}), fetched_at=record.fetched_at.isoformat(), created_at=record.created_at.isoformat())

    def create_discovery_item(self, workspace_id: str, created_by: str, body: DiscoveryItemCreate) -> DiscoveryItem:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            record = DiscoveryItemRecord(id=str(uuid4()), workspace_id=workspace_id, created_by=created_by, provider=body.provider, provider_paper_id=body.provider_paper_id, classification=body.classification, review_status="pending", title=body.title, abstract=body.abstract, source_quote=body.source_quote, source_url=body.source_url, license=body.license, rate_limit_policy=body.rate_limit_policy, snapshot=body.snapshot, fetched_at=now, created_at=now)
            session.add(record); session.flush()
            return self._discovery_item_model(record)

    def list_discovery_items(self, workspace_id: str, review_status: str = "pending") -> list[DiscoveryItem]:
        with self.session_factory() as session:
            return [self._discovery_item_model(row) for row in session.scalars(select(DiscoveryItemRecord).where(DiscoveryItemRecord.workspace_id == workspace_id, DiscoveryItemRecord.review_status == review_status).order_by(DiscoveryItemRecord.created_at.desc())).all()]

    def review_discovery_item(self, workspace_id: str, item_id: str, review_status: str) -> DiscoveryItem:
        with self.session_factory.begin() as session:
            record = session.scalar(select(DiscoveryItemRecord).where(DiscoveryItemRecord.workspace_id == workspace_id, DiscoveryItemRecord.id == item_id).with_for_update())
            if record is None: raise PaperNotFoundError(item_id)
            record.review_status = review_status
            return self._discovery_item_model(record)
    @staticmethod
    def _hypothesis_card_model(record: HypothesisCardRecord) -> HypothesisCard:
        return HypothesisCard(
            id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
            claim=record.claim, mechanism=record.mechanism, target=record.target,
            conditions=record.conditions, intervention=record.intervention, outcome=record.outcome,
            direction=record.direction, assumptions=list(record.assumptions or []),
            competing_theories=list(record.competing_theories or []), predictions=list(record.predictions or []),
            falsifiers=list(record.falsifiers or []), test=record.test, status=record.status,
            human_reviewed=record.human_reviewed, empirically_supported=record.empirically_supported,
            metadata=dict(record.metadata_json or {}),
            created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
        )

    def create_hypothesis_card(self, workspace_id: str, created_by: str, body: HypothesisCardCreate) -> HypothesisCard:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            record = HypothesisCardRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by, claim=body.claim,
                mechanism=body.mechanism, target=body.target, conditions=body.conditions,
                intervention=body.intervention, outcome=body.outcome, direction=body.direction,
                assumptions=body.assumptions, competing_theories=body.competing_theories,
                predictions=body.predictions, falsifiers=body.falsifiers, test=body.test,
                status="draft", human_reviewed=False, empirically_supported=False,
                metadata_json={},
                created_at=now, updated_at=now,
            )
            session.add(record)
            session.flush()
            return self._hypothesis_card_model(record)

    def list_hypothesis_cards(self, workspace_id: str) -> list[HypothesisCard]:
        with self.session_factory() as session:
            return [self._hypothesis_card_model(row) for row in session.scalars(select(HypothesisCardRecord).where(HypothesisCardRecord.workspace_id == workspace_id).order_by(HypothesisCardRecord.updated_at.desc())).all()]

    def get_hypothesis_card(self, workspace_id: str, card_id: str) -> HypothesisCard:
        with self.session_factory() as session:
            record = session.scalar(select(HypothesisCardRecord).where(
                HypothesisCardRecord.workspace_id == workspace_id,
                HypothesisCardRecord.id == card_id,
            ))
            if record is None:
                raise PaperNotFoundError(card_id)
            return self._hypothesis_card_model(record)

    def set_hypothesis_card_status(self, workspace_id: str, card_id: str, status: str, *, human_reviewed: bool | None = None, empirically_supported: bool | None = None) -> HypothesisCard:
        with self.session_factory.begin() as session:
            record = session.scalar(select(HypothesisCardRecord).where(HypothesisCardRecord.workspace_id == workspace_id, HypothesisCardRecord.id == card_id).with_for_update())
            if record is None: raise PaperNotFoundError(card_id)
            if status in {"reviewable", "reviewed", "supported"} and (not record.competing_theories or not record.falsifiers):
                raise ValueError("reviewable hypothesis requires a competing theory and at least one falsifier")
            if status == "reviewed" and human_reviewed is False:
                raise ValueError("reviewed status requires human_reviewed")
            if status == "supported" and empirically_supported is False:
                raise ValueError("supported status requires empirically_supported")
            record.status = status
            if human_reviewed is not None: record.human_reviewed = human_reviewed
            if empirically_supported is not None: record.empirically_supported = empirically_supported
            record.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self._hypothesis_card_model(record)
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

    @staticmethod
    def _require_workspace_owner(
        session: Session, actor_id: str, workspace_id: str,
    ) -> WorkspaceRecord:
        """Lock the workspace and require an owner membership for member administration."""
        workspace = session.scalar(
            select(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id).with_for_update()
        )
        if workspace is None:
            raise WorkspaceAccessError(workspace_id)
        membership = session.get(WorkspaceMemberRecord, (workspace_id, actor_id))
        if membership is None:
            raise WorkspaceAccessError(workspace_id)
        if membership.role != "owner":
            raise WorkspacePermissionError(workspace_id)
        return workspace

    @staticmethod
    def _require_remaining_owner(
        session: Session, workspace: WorkspaceRecord, member: WorkspaceMemberRecord,
    ) -> None:
        """Protect the personal-workspace invariant and prevent owner-less projects."""
        if workspace.personal_owner_id == member.user_id:
            raise WorkspacePermissionError("the personal workspace owner must remain an owner")
        if member.role != "owner":
            return
        owner_count = session.scalar(
            select(func.count()).select_from(WorkspaceMemberRecord).where(
                WorkspaceMemberRecord.workspace_id == workspace.id,
                WorkspaceMemberRecord.role == "owner",
            )
        )
        if int(owner_count or 0) <= 1:
            raise WorkspacePermissionError("a workspace must retain at least one owner")

    def list_workspace_members(self, workspace_id: str) -> list[WorkspaceMember]:
        with self.session_factory() as session:
            rows = session.execute(
                select(WorkspaceMemberRecord, UserRecord)
                .join(UserRecord, UserRecord.id == WorkspaceMemberRecord.user_id)
                .where(WorkspaceMemberRecord.workspace_id == workspace_id)
                .order_by(WorkspaceMemberRecord.created_at, UserRecord.id)
            ).all()
            return [_workspace_member_model(member, user) for member, user in rows]

    def add_workspace_member_for_owner(
        self, actor_id: str, workspace_id: str, *, issuer: str, email: str | None,
        subject: str | None, role: str,
    ) -> WorkspaceMember:
        with self.session_factory.begin() as session:
            self._require_workspace_owner(session, actor_id, workspace_id)
            statement = select(UserRecord).where(UserRecord.issuer == issuer)
            if email:
                statement = statement.where(UserRecord.email == email)
            else:
                statement = statement.where(UserRecord.subject == subject)
            users = session.scalars(statement).all()
            if not users:
                raise WorkspaceMemberNotFoundError(email or subject or "member")
            if len(users) != 1:
                raise WorkspaceMemberConflictError("member identity is ambiguous")
            user = users[0]
            if session.get(WorkspaceMemberRecord, (workspace_id, user.id)) is not None:
                raise WorkspaceMemberConflictError("member already belongs to this workspace")
            member = WorkspaceMemberRecord(
                workspace_id=workspace_id, user_id=user.id, role=role,
                created_at=datetime.now(timezone.utc),
            )
            session.add(member)
            session.flush()
            return _workspace_member_model(member, user)

    def update_workspace_member_for_owner(
        self, actor_id: str, workspace_id: str, member_user_id: str, *, role: str,
    ) -> WorkspaceMember:
        with self.session_factory.begin() as session:
            workspace = self._require_workspace_owner(session, actor_id, workspace_id)
            member = session.get(WorkspaceMemberRecord, (workspace_id, member_user_id))
            if member is None:
                raise WorkspaceMemberNotFoundError(member_user_id)
            if member.role != role:
                if role != "owner":
                    self._require_remaining_owner(session, workspace, member)
                member.role = role
            user = session.get(UserRecord, member.user_id)
            if user is None:  # Defensive: the FK normally makes this unreachable.
                raise WorkspaceMemberNotFoundError(member_user_id)
            session.flush()
            return _workspace_member_model(member, user)

    def remove_workspace_member_for_owner(
        self, actor_id: str, workspace_id: str, member_user_id: str,
    ) -> None:
        with self.session_factory.begin() as session:
            workspace = self._require_workspace_owner(session, actor_id, workspace_id)
            member = session.get(WorkspaceMemberRecord, (workspace_id, member_user_id))
            if member is None:
                raise WorkspaceMemberNotFoundError(member_user_id)
            self._require_remaining_owner(session, workspace, member)
            session.delete(member)

    # --- Research workspace assets ---------------------------------------

    @staticmethod
    def _scoped_research_question(
        session: Session, workspace_id: str, question_id: str,
    ) -> ResearchQuestionRecord:
        record = session.scalar(select(ResearchQuestionRecord).where(
            ResearchQuestionRecord.workspace_id == workspace_id,
            ResearchQuestionRecord.id == question_id,
        ))
        if record is None:
            raise PaperNotFoundError(question_id)
        return record

    @staticmethod
    def _scoped_source_set(session: Session, workspace_id: str, source_set_id: str) -> SourceSetRecord:
        record = session.scalar(select(SourceSetRecord).where(
            SourceSetRecord.workspace_id == workspace_id,
            SourceSetRecord.id == source_set_id,
        ))
        if record is None:
            raise PaperNotFoundError(source_set_id)
        return record

    @staticmethod
    def _source_set_paper_ids(session: Session, source_set_id: str) -> list[str]:
        return list(session.scalars(select(SourceSetPaperRecord.paper_id).where(
            SourceSetPaperRecord.source_set_id == source_set_id,
        ).order_by(SourceSetPaperRecord.ordinal, SourceSetPaperRecord.id)).all())

    @staticmethod
    def _validate_workspace_paper_ids(session: Session, workspace_id: str, paper_ids: list[str]) -> list[str]:
        unique_ids = list(dict.fromkeys(paper_ids))
        if len(unique_ids) != len(paper_ids):
            raise ValueError("source set paper_ids must not contain duplicates")
        if not unique_ids:
            return []
        found = session.scalars(select(PaperRecord.id).where(
            PaperRecord.workspace_id == workspace_id,
            PaperRecord.id.in_(unique_ids),
        )).all()
        if len(found) != len(unique_ids):
            raise PaperNotFoundError("paper")
        return unique_ids

    def list_research_questions(self, workspace_id: str) -> list[ResearchQuestion]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            rows = session.scalars(select(ResearchQuestionRecord).where(
                ResearchQuestionRecord.workspace_id == workspace_id,
            ).order_by(ResearchQuestionRecord.updated_at.desc(), ResearchQuestionRecord.id)).all()
            return [_research_question_model(row) for row in rows]

    def get_research_question(self, workspace_id: str, question_id: str) -> ResearchQuestion:
        with self.session_factory() as session:
            return _research_question_model(self._scoped_research_question(session, workspace_id, question_id))

    def create_research_question(
        self, workspace_id: str, created_by: str, *, title: str = "", question: str,
    ) -> ResearchQuestion:
        if not question.strip():
            raise ValueError("research question is required")
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            record = ResearchQuestionRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                title=title.strip(), question=question.strip(), created_at=now, updated_at=now,
            )
            session.add(record)
            return _research_question_model(record)

    def update_research_question(
        self, workspace_id: str, question_id: str, *, title: str | None = None, question: str | None = None,
    ) -> ResearchQuestion:
        if question is not None and not question.strip():
            raise ValueError("research question is required")
        with self.session_factory.begin() as session:
            record = self._scoped_research_question(session, workspace_id, question_id)
            if title is not None:
                record.title = title.strip()
            if question is not None:
                record.question = question.strip()
            record.updated_at = datetime.now(timezone.utc)
            return _research_question_model(record)

    def delete_research_question(self, workspace_id: str, question_id: str) -> bool:
        with self.session_factory.begin() as session:
            record = session.scalar(select(ResearchQuestionRecord).where(
                ResearchQuestionRecord.workspace_id == workspace_id,
                ResearchQuestionRecord.id == question_id,
            ))
            if record is None:
                return False
            session.delete(record)
            return True

    def list_source_sets(self, workspace_id: str) -> list[SourceSet]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            rows = session.scalars(select(SourceSetRecord).where(
                SourceSetRecord.workspace_id == workspace_id,
            ).order_by(SourceSetRecord.updated_at.desc(), SourceSetRecord.id)).all()
            return [_source_set_model(row, self._source_set_paper_ids(session, row.id)) for row in rows]

    def get_source_set(self, workspace_id: str, source_set_id: str) -> SourceSet:
        with self.session_factory() as session:
            record = self._scoped_source_set(session, workspace_id, source_set_id)
            return _source_set_model(record, self._source_set_paper_ids(session, record.id))

    def create_source_set(
        self, workspace_id: str, created_by: str, *, name: str, description: str = "", paper_ids: list[str] | None = None,
    ) -> SourceSet:
        if not name.strip():
            raise ValueError("source set name is required")
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            selected_ids = self._validate_workspace_paper_ids(session, workspace_id, paper_ids or [])
            record = SourceSetRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                name=name.strip(), description=description.strip(), created_at=now, updated_at=now,
            )
            session.add(record)
            # SourceSetPaperRecord intentionally has no ORM relationship to its
            # parent.  Flush the parent explicitly so SQLAlchemy cannot emit the
            # association rows before the source_sets row on FK-enforcing
            # databases (SQLite exposed this ordering bug first).
            session.flush([record])
            session.add_all(SourceSetPaperRecord(
                id=str(uuid4()), source_set_id=record.id, paper_id=paper_id, ordinal=index,
            ) for index, paper_id in enumerate(selected_ids, start=1))
            session.flush()
            return _source_set_model(record, selected_ids)

    def update_source_set(
        self, workspace_id: str, source_set_id: str, *, name: str | None = None,
        description: str | None = None, paper_ids: list[str] | None = None,
    ) -> SourceSet:
        if name is not None and not name.strip():
            raise ValueError("source set name is required")
        with self.session_factory.begin() as session:
            record = self._scoped_source_set(session, workspace_id, source_set_id)
            if name is not None:
                record.name = name.strip()
            if description is not None:
                record.description = description.strip()
            selected_ids = self._source_set_paper_ids(session, record.id)
            if paper_ids is not None:
                selected_ids = self._validate_workspace_paper_ids(session, workspace_id, paper_ids)
                session.execute(delete(SourceSetPaperRecord).where(SourceSetPaperRecord.source_set_id == record.id))
                session.add_all(SourceSetPaperRecord(
                    id=str(uuid4()), source_set_id=record.id, paper_id=paper_id, ordinal=index,
                ) for index, paper_id in enumerate(selected_ids, start=1))
            record.updated_at = datetime.now(timezone.utc)
            # Execute replacement inserts inside this transaction, rather than
            # deferring them until the context manager commits after return.
            session.flush()
            return _source_set_model(record, selected_ids)

    def delete_source_set(self, workspace_id: str, source_set_id: str) -> bool:
        with self.session_factory.begin() as session:
            record = session.scalar(select(SourceSetRecord).where(
                SourceSetRecord.workspace_id == workspace_id,
                SourceSetRecord.id == source_set_id,
            ))
            if record is None:
                return False
            session.delete(record)
            return True

    @staticmethod
    def _scoped_research_run(session: Session, workspace_id: str, run_id: str) -> ResearchRunRecord:
        record = session.scalar(select(ResearchRunRecord).where(
            ResearchRunRecord.workspace_id == workspace_id, ResearchRunRecord.id == run_id,
        ))
        if record is None:
            raise PaperNotFoundError(run_id)
        return record

    @staticmethod
    def _run_artifacts(session: Session, run_id: str) -> list[RunArtifactRecord]:
        return session.scalars(select(RunArtifactRecord).where(
            RunArtifactRecord.research_run_id == run_id,
        ).order_by(RunArtifactRecord.ordinal, RunArtifactRecord.id)).all()

    def create_research_run(
        self, workspace_id: str, created_by: str, *, research_question_id: str | None = None,
        source_set_id: str | None = None, source_paper_ids: list[str] | None = None,
        excluded_paper_ids: list[str] | None = None, purpose: str = "", success_criteria: str = "",
        plan: dict | list | None = None, model: str = "", prompt_version: str = "",
    ) -> ResearchRun:
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            question = ""
            if research_question_id:
                question_record = self._scoped_research_question(session, workspace_id, research_question_id)
                question = question_record.question
            selected_ids = self._validate_workspace_paper_ids(session, workspace_id, source_paper_ids or [])
            if source_set_id:
                source_set = self._scoped_source_set(session, workspace_id, source_set_id)
                source_set_ids = self._source_set_paper_ids(session, source_set.id)
                if selected_ids and selected_ids != source_set_ids:
                    raise ValueError("source_paper_ids must match the selected source set snapshot")
                selected_ids = source_set_ids
            excluded_ids = self._validate_workspace_paper_ids(session, workspace_id, excluded_paper_ids or [])
            if set(selected_ids) & set(excluded_ids):
                raise ValueError("a source paper cannot also be excluded")
            now = datetime.now(timezone.utc)
            record = ResearchRunRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                research_question_id=research_question_id, source_set_id=source_set_id,
                research_question=question, source_paper_ids=selected_ids,
                excluded_paper_ids=excluded_ids, purpose=purpose.strip(),
                success_criteria=success_criteria.strip(), plan=plan if plan is not None else {},
                model=model.strip(), prompt_version=prompt_version.strip(), status="queued",
                cancel_requested=False, created_at=now,
            )
            session.add(record)
            return _research_run_model(record, [])

    def list_research_runs(self, workspace_id: str) -> list[ResearchRun]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            records = session.scalars(select(ResearchRunRecord).where(
                ResearchRunRecord.workspace_id == workspace_id,
            ).order_by(ResearchRunRecord.created_at.desc(), ResearchRunRecord.id)).all()
            return [_research_run_model(record, self._run_artifacts(session, record.id)) for record in records]

    def get_research_run(self, workspace_id: str, run_id: str) -> ResearchRun:
        with self.session_factory() as session:
            record = self._scoped_research_run(session, workspace_id, run_id)
            return _research_run_model(record, self._run_artifacts(session, record.id))

    def start_research_run(self, workspace_id: str, run_id: str) -> ResearchRun:
        with self.session_factory.begin() as session:
            record = self._scoped_research_run(session, workspace_id, run_id)
            if record.cancel_requested or record.status == "cancelled":
                raise ValueError("research run was cancelled")
            if record.status != "queued":
                raise ValueError("research run must be queued before it can start")
            record.status, record.started_at = "running", datetime.now(timezone.utc)
            return _research_run_model(record, self._run_artifacts(session, record.id))

    def append_run_artifact(self, workspace_id: str, run_id: str, *, kind: str, payload: dict | list) -> RunArtifact:
        if not kind.strip():
            raise ValueError("run artifact kind is required")
        with self.session_factory.begin() as session:
            self._scoped_research_run(session, workspace_id, run_id)
            ordinal = (session.scalar(select(func.max(RunArtifactRecord.ordinal)).where(
                RunArtifactRecord.research_run_id == run_id,
            )) or 0) + 1
            record = RunArtifactRecord(
                id=str(uuid4()), research_run_id=run_id, kind=kind.strip(), payload=payload,
                ordinal=ordinal, created_at=datetime.now(timezone.utc),
            )
            session.add(record)
            return _run_artifact_model(record)

    def finish_research_run(self, workspace_id: str, run_id: str, *, status: str) -> ResearchRun:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("invalid terminal research run status")
        with self.session_factory.begin() as session:
            record = self._scoped_research_run(session, workspace_id, run_id)
            if record.status not in {"queued", "running"}:
                raise ValueError("research run is already terminal")
            record.status, record.completed_at = status, datetime.now(timezone.utc)
            return _research_run_model(record, self._run_artifacts(session, record.id))

    def cancel_research_run(self, workspace_id: str, run_id: str) -> ResearchRun:
        with self.session_factory.begin() as session:
            record = self._scoped_research_run(session, workspace_id, run_id)
            if record.status in {"succeeded", "failed"}:
                raise ValueError("completed research runs cannot be cancelled")
            record.cancel_requested = True
            if record.status == "queued":
                record.status, record.completed_at = "cancelled", datetime.now(timezone.utc)
            return _research_run_model(record, self._run_artifacts(session, record.id))

    def research_run_cancel_requested(self, workspace_id: str, run_id: str) -> bool:
        with self.session_factory() as session:
            return self._scoped_research_run(session, workspace_id, run_id).cancel_requested

    def list_ideas(self, workspace_id: str) -> list[Idea]:
        with self.session_factory() as s:
            self._require_workspace(s, workspace_id)
            return [_idea_model(x) for x in s.scalars(select(IdeaRecord).where(IdeaRecord.workspace_id == workspace_id).order_by(IdeaRecord.created_at.desc())).all()]

    def _validate_idea_anchors(
        self, session: Session, workspace_id: str, *, research_run_id: str | None,
        paper_id: str | None, source_span_id: str | None,
    ) -> None:
        if research_run_id:
            self._scoped_research_run(session, workspace_id, research_run_id)
        if paper_id and not session.scalar(select(PaperRecord.id).where(
            PaperRecord.id == paper_id, PaperRecord.workspace_id == workspace_id,
        )):
            raise PaperNotFoundError(paper_id)
        if source_span_id:
            span = self._scoped_span(session, workspace_id, source_span_id)
            if paper_id:
                source_paper_id = session.scalar(select(SourceVersionRecord.paper_id).where(
                    SourceVersionRecord.id == span.source_version_id,
                    SourceVersionRecord.workspace_id == workspace_id,
                ))
                if source_paper_id != paper_id:
                    raise ValueError("idea paper_id and source_span_id must reference the same paper")

    def create_idea(self, workspace_id: str, user_id: str, body: IdeaCreate) -> Idea:
        with self.session_factory.begin() as s:
            self._require_workspace(s, workspace_id)
            self._validate_idea_anchors(
                s, workspace_id, research_run_id=body.research_run_id,
                paper_id=body.paper_id, source_span_id=body.source_span_id,
            )
            x = IdeaRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=user_id,
                kind=body.kind, content=body.content.strip(),
                research_run_id=body.research_run_id, claim_id=body.claim_id,
                paper_id=body.paper_id, source_span_id=body.source_span_id,
                checklist=dict(body.checklist), status="unverified",
                created_at=datetime.now(timezone.utc),
            )
            s.add(x)
            s.flush()
            return _idea_model(x)

    def update_idea(self, workspace_id: str, idea_id: str, body: IdeaUpdate) -> Idea:
        with self.session_factory.begin() as session:
            idea = session.scalar(select(IdeaRecord).where(
                IdeaRecord.id == idea_id, IdeaRecord.workspace_id == workspace_id,
            ).with_for_update())
            if idea is None:
                raise PaperNotFoundError(idea_id)
            if idea.status == "promoted":
                raise ValueError("promoted ideas are immutable")
            fields = body.model_fields_set
            run_id = body.research_run_id if "research_run_id" in fields else idea.research_run_id
            paper_id = body.paper_id if "paper_id" in fields else idea.paper_id
            span_id = body.source_span_id if "source_span_id" in fields else idea.source_span_id
            self._validate_idea_anchors(
                session, workspace_id, research_run_id=run_id,
                paper_id=paper_id, source_span_id=span_id,
            )
            if "kind" in fields and body.kind is not None:
                idea.kind = body.kind
            if "content" in fields and body.content is not None:
                idea.content = body.content.strip()
            if "research_run_id" in fields:
                idea.research_run_id = body.research_run_id
            if "claim_id" in fields:
                idea.claim_id = body.claim_id
            if "paper_id" in fields:
                idea.paper_id = body.paper_id
            if "source_span_id" in fields:
                idea.source_span_id = body.source_span_id
            if "checklist" in fields and body.checklist is not None:
                idea.checklist = dict(body.checklist)
            session.flush()
            return _idea_model(idea)

    def promote_idea(self, workspace_id: str, user_id: str, idea_id: str) -> Idea:
        with self.session_factory.begin() as s:
            idea=s.scalar(select(IdeaRecord).where(IdeaRecord.id==idea_id, IdeaRecord.workspace_id==workspace_id).with_for_update())
            if idea is None: raise PaperNotFoundError(idea_id)
            if idea.status == "promoted": raise ValueError("idea is already promoted")
            if not all(idea.checklist.get(k) is True for k in ("evidence", "falsifier", "test")): raise ValueError("promotion requires evidence, falsifier, and test checklist")
            now = datetime.now(timezone.utc)
            paper_snapshot = None
            if idea.paper_id:
                paper = s.scalar(select(PaperRecord).where(
                    PaperRecord.id == idea.paper_id,
                    PaperRecord.workspace_id == workspace_id,
                ))
                if paper is not None:
                    paper_snapshot = {
                        "id": paper.id, "title": paper.title,
                        "content_hash": paper.content_hash, "year": paper.year,
                    }
            source_snapshot = None
            if idea.source_span_id:
                span = s.scalar(select(SourceSpanRecord).where(
                    SourceSpanRecord.id == idea.source_span_id,
                    SourceSpanRecord.workspace_id == workspace_id,
                ))
                if span is not None:
                    version = s.scalar(select(SourceVersionRecord).where(
                        SourceVersionRecord.id == span.source_version_id,
                        SourceVersionRecord.workspace_id == workspace_id,
                    ))
                    source_snapshot = {
                        "source_version": {
                            "id": version.id, "kind": version.kind,
                            "locator": version.locator,
                            "content_hash": version.content_hash,
                        } if version is not None else None,
                        "span": {
                            "id": span.id, "page": span.page,
                            "line_start": span.line_start, "line_end": span.line_end,
                            "char_start": span.char_start, "char_end": span.char_end,
                            "locator": dict(span.locator_json or {}),
                            "text": span.text, "verbatim_quote": span.text,
                        },
                    }
            run_snapshot = None
            if idea.research_run_id:
                run = s.scalar(select(ResearchRunRecord).where(
                    ResearchRunRecord.id == idea.research_run_id,
                    ResearchRunRecord.workspace_id == workspace_id,
                ))
                if run is not None:
                    run_snapshot = {
                        "id": run.id, "purpose": run.purpose,
                        "research_question": run.research_question,
                        "success_criteria": run.success_criteria,
                        "status": run.status, "source_paper_ids": list(run.source_paper_ids or []),
                        "prompt_version": run.prompt_version, "model": run.model,
                    }
            anchor_snapshot = {
                "idea_id": idea.id, "idea_kind": idea.kind,
                "research_run_id": idea.research_run_id, "claim_id": idea.claim_id,
                "paper_id": idea.paper_id, "source_span_id": idea.source_span_id,
                "checklist": dict(idea.checklist or {}), "captured_at": now.isoformat(),
                "paper": paper_snapshot, "source": source_snapshot,
                "research_run": run_snapshot,
            }
            card=HypothesisCardRecord(id=str(uuid4()),workspace_id=workspace_id,created_by=user_id,claim=idea.content,mechanism="",target="",conditions="",intervention="",outcome="",direction="",assumptions=[],competing_theories=[],predictions=[],falsifiers=[],test="",status="draft",human_reviewed=False,empirically_supported=False,metadata_json={"idea_anchor_snapshot": anchor_snapshot},created_at=now,updated_at=now);s.add(card)
            # The scalar FK does not establish ORM ordering; persist the card
            # before linking the idea to it.
            s.flush([card])
            idea.status="promoted";idea.hypothesis_card_id=card.id
            s.flush()
            return _idea_model(idea)

    @staticmethod
    def _review_thread_model(session: Session, record: ReviewThreadRecord) -> ReviewThread:
        comments = session.scalars(select(ReviewCommentRecord).where(
            ReviewCommentRecord.review_thread_id == record.id,
        ).order_by(ReviewCommentRecord.created_at, ReviewCommentRecord.id)).all()
        decisions = session.scalars(select(ReviewDecisionRecord).where(
            ReviewDecisionRecord.review_thread_id == record.id,
        ).order_by(ReviewDecisionRecord.created_at, ReviewDecisionRecord.id)).all()
        evidence_snapshot = None
        if record.evidence_ref_id:
            evidence = session.scalar(select(EvidenceRefRecord).where(
                EvidenceRefRecord.id == record.evidence_ref_id,
                EvidenceRefRecord.workspace_id == record.workspace_id,
            ))
            if evidence is not None:
                evidence_snapshot = {
                    "id": evidence.id, "target_claim": evidence.target_claim,
                    "role": evidence.role, "extraction_quality": evidence.extraction_quality,
                    "source_version_id": evidence.source_version_id,
                    "source_span_id": evidence.source_span_id,
                    "quote_start": evidence.quote_start, "quote_end": evidence.quote_end,
                    "verbatim_quote": evidence.verbatim_quote,
                }
        return ReviewThread(
            id=record.id, workspace_id=record.workspace_id, created_by=record.created_by,
            title=record.title, research_run_id=record.research_run_id,
            claim_id=record.claim_id, claim_artifact_id=record.claim_artifact_id,
            claim_snapshot=dict(record.claim_snapshot or {}) if record.claim_snapshot is not None else None,
            evidence_link_id=record.evidence_ref_id, evidence_snapshot=evidence_snapshot,
            assigned_to=record.assigned_to, status=record.status,
            comments=[ReviewComment(
                id=item.id, author_id=item.author_id, body=item.body,
                created_at=item.created_at.isoformat(),
            ) for item in comments],
            decisions=[ReviewDecision(
                id=item.id, decided_by=item.decided_by, verdict=item.verdict,
                reason=item.reason, created_at=item.created_at.isoformat(),
            ) for item in decisions],
            created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
        )

    @staticmethod
    def _scoped_review_thread(session: Session, workspace_id: str, thread_id: str) -> ReviewThreadRecord:
        record = session.scalar(select(ReviewThreadRecord).where(
            ReviewThreadRecord.id == thread_id,
            ReviewThreadRecord.workspace_id == workspace_id,
        ))
        if record is None:
            raise PaperNotFoundError(thread_id)
        return record

    @staticmethod
    def _validate_review_assignee(session: Session, workspace_id: str, assigned_to: str | None) -> None:
        if assigned_to:
            membership = session.get(WorkspaceMemberRecord, (workspace_id, assigned_to))
            if membership is None or membership.role not in {"owner", "editor"}:
                raise PaperNotFoundError(assigned_to)

    def create_review_thread(
        self, workspace_id: str, created_by: str, body: ReviewThreadCreate,
    ) -> ReviewThread:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            self._validate_review_assignee(session, workspace_id, body.assigned_to)
            if body.evidence_link_id:
                evidence = session.scalar(select(EvidenceRefRecord.id).where(
                    EvidenceRefRecord.id == body.evidence_link_id,
                    EvidenceRefRecord.workspace_id == workspace_id,
                ))
                if evidence is None:
                    raise PaperNotFoundError(body.evidence_link_id)
                claim_artifact_id = None
                claim_snapshot = None
            else:
                run = self._scoped_research_run(session, workspace_id, body.research_run_id or "")
                matches: list[tuple[RunArtifactRecord, dict]] = []
                for artifact in self._run_artifacts(session, run.id):
                    if artifact.kind != "validation" or not isinstance(artifact.payload, dict):
                        continue
                    claims = artifact.payload.get("claims")
                    if not isinstance(claims, list):
                        continue
                    for claim in claims:
                        if (
                            isinstance(claim, dict)
                            and claim.get("claim_id") == body.claim_id
                            and isinstance(claim.get("text"), str)
                            and claim["text"].strip()
                        ):
                            matches.append((artifact, dict(claim)))
                if len(matches) != 1:
                    raise ValueError(
                        "claim_id must resolve to exactly one immutable validation artifact"
                    )
                claim_artifact_id, claim_snapshot = matches[0][0].id, matches[0][1]
            record = ReviewThreadRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                title=body.title, research_run_id=body.research_run_id,
                claim_id=body.claim_id, evidence_ref_id=body.evidence_link_id,
                claim_artifact_id=claim_artifact_id, claim_snapshot=claim_snapshot,
                assigned_to=body.assigned_to, status="open", created_at=now, updated_at=now,
            )
            session.add(record)
            session.flush()
            return self._review_thread_model(session, record)

    def list_review_threads(self, workspace_id: str) -> list[ReviewThread]:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            rows = session.scalars(select(ReviewThreadRecord).where(
                ReviewThreadRecord.workspace_id == workspace_id,
            ).order_by(ReviewThreadRecord.updated_at.desc(), ReviewThreadRecord.id)).all()
            return [self._review_thread_model(session, row) for row in rows]

    def get_review_thread(self, workspace_id: str, thread_id: str) -> ReviewThread:
        with self.session_factory() as session:
            return self._review_thread_model(
                session, self._scoped_review_thread(session, workspace_id, thread_id),
            )

    def assign_review_thread(
        self, workspace_id: str, thread_id: str, assigned_to: str | None,
    ) -> ReviewThread:
        with self.session_factory.begin() as session:
            record = self._scoped_review_thread(session, workspace_id, thread_id)
            self._validate_review_assignee(session, workspace_id, assigned_to)
            record.assigned_to = assigned_to
            record.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self._review_thread_model(session, record)

    def add_review_comment(
        self, workspace_id: str, thread_id: str, author_id: str, body: str,
    ) -> ReviewThread:
        with self.session_factory.begin() as session:
            record = self._scoped_review_thread(session, workspace_id, thread_id)
            now = datetime.now(timezone.utc)
            session.add(ReviewCommentRecord(
                id=str(uuid4()), review_thread_id=record.id, author_id=author_id,
                body=body, created_at=now,
            ))
            record.updated_at = now
            session.flush()
            return self._review_thread_model(session, record)

    def add_review_decision(
        self, workspace_id: str, thread_id: str, decided_by: str, *, verdict: str, reason: str,
    ) -> ReviewThread:
        with self.session_factory.begin() as session:
            record = self._scoped_review_thread(session, workspace_id, thread_id)
            now = datetime.now(timezone.utc)
            session.add(ReviewDecisionRecord(
                id=str(uuid4()), review_thread_id=record.id, decided_by=decided_by,
                verdict=verdict, reason=reason, created_at=now,
            ))
            record.status = "resolved" if verdict in {"accepted", "rejected"} else "open"
            record.updated_at = now
            session.flush()
            return self._review_thread_model(session, record)

    def export_review_report_markdown(self, workspace_id: str) -> str:
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            workspace = session.get(WorkspaceRecord, workspace_id)
            rows = session.scalars(select(ReviewThreadRecord).where(
                ReviewThreadRecord.workspace_id == workspace_id,
            ).order_by(ReviewThreadRecord.created_at, ReviewThreadRecord.id)).all()
            lines = ["# Review report", "", f"Workspace: {_markdown_literal(workspace.name)}", ""]
            for index, row in enumerate(rows, 1):
                thread = self._review_thread_model(session, row)
                lines.extend([
                    f"## Review thread {index}", "",
                    f"- Title: {_markdown_literal(thread.title)}",
                    f"- Status: {_markdown_literal(thread.status)}",
                    f"- Assigned to: {_markdown_literal(thread.assigned_to or 'unassigned')}",
                ])
                if row.evidence_ref_id:
                    evidence = session.scalar(select(EvidenceRefRecord).where(
                        EvidenceRefRecord.id == row.evidence_ref_id,
                        EvidenceRefRecord.workspace_id == workspace_id,
                    ))
                    if evidence is not None:
                        span = session.get(SourceSpanRecord, evidence.source_span_id)
                        version = session.get(SourceVersionRecord, evidence.source_version_id)
                        paper = session.get(PaperRecord, version.paper_id) if version and version.paper_id else None
                        lines.extend([
                            f"- EvidenceLink: {_markdown_literal(evidence.id)}",
                            f"- Evidence role: {_markdown_literal(evidence.role)}",
                            f"- Extraction quality: {_markdown_literal(evidence.extraction_quality)}",
                            f"- Target claim: {_markdown_literal(evidence.target_claim)}",
                            f"- Source: {_markdown_literal(paper.title if paper else (version.locator if version else 'unknown'))}",
                            f"- Page: {_markdown_literal(span.page if span else None)}",
                            f"- Source Version: {_markdown_literal(evidence.source_version_id)}",
                            f"- Source Span: {_markdown_literal(evidence.source_span_id)}",
                            "", "### Verbatim evidence", "",
                            _markdown_block(evidence.verbatim_quote),
                        ])
                else:
                    lines.extend([
                        f"- Research Run: {_markdown_literal(row.research_run_id)}",
                        f"- Claim ID: {_markdown_literal(row.claim_id)}",
                        f"- Validation Artifact: {_markdown_literal(row.claim_artifact_id)}",
                        f"- Claim text: {_markdown_literal((row.claim_snapshot or {}).get('text', ''))}",
                        f"- Claim classification: {_markdown_literal((row.claim_snapshot or {}).get('classification'))}",
                        f"- Citation IDs: {_markdown_literal((row.claim_snapshot or {}).get('citation_ids', []))}",
                    ])
                if thread.decisions:
                    lines.extend(["", "### Decisions", ""])
                    lines.extend(
                        f"- Verdict: {_markdown_literal(item.verdict)}; reason: {_markdown_literal(item.reason)}; actor: {_markdown_literal(item.decided_by or 'deleted user')}"
                        for item in thread.decisions
                    )
                if thread.comments:
                    lines.extend(["", "### Comments", ""])
                    lines.extend(
                        f"- Body: {_markdown_literal(item.body)}; author: {_markdown_literal(item.author_id or 'deleted user')}"
                        for item in thread.comments
                    )
                lines.extend([""])
            return "\n".join(lines).rstrip() + "\n"

    def list(self, workspace_id: str) -> list[Paper]:
        with self.session_factory() as session:
            records = session.scalars(
                select(PaperRecord)
                .where(PaperRecord.workspace_id == workspace_id)
                .options(selectinload(PaperRecord.chunks))
            ).all()
            return [_to_model(record) for record in records]

    def search_chunk_candidates(
        self, workspace_id: str, query: str, *, limit: int = 8,
        paper_ids: list[str] | None = None, year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[Paper]:
        """Load a bounded, portable lexical candidate pool for request-time RAG.

        The database performs the coarse filtering. Python scoring and optional
        vector fusion only see at most ``min(max(4 * limit, 32), 200)`` chunks.
        Zero-score rows are retained as a deterministic fallback for queries
        that SQLite/PostgreSQL ``LIKE`` cannot tokenize usefully.
        """
        pool_size = min(max(4 * max(1, limit), 32), 200)
        scoped_ids = list(dict.fromkeys(paper_ids or []))[:500]
        terms = list(dict.fromkeys(
            re.findall(r"[a-z0-9][a-z0-9_-]+|[一-龯ぁ-んァ-ヶ]{2,}", query.casefold())
        ))[:12]
        escaped_terms = [
            term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            for term in terms
        ]
        with self.session_factory() as session:
            searchable = func.lower(
                PaperRecord.title + " " + PaperRecord.abstract + " " + ChunkRecord.text
            )
            lexical_score = sum(
                (case((searchable.like(f"%{term}%", escape="\\"), 1), else_=0) for term in escaped_terms),
                start=case((ChunkRecord.id.is_not(None), 0), else_=0),
            )
            statement = (
                select(PaperRecord, ChunkRecord)
                .join(ChunkRecord, ChunkRecord.paper_id == PaperRecord.id)
                .where(
                    PaperRecord.workspace_id == workspace_id,
                    PaperRecord.status == "ready",
                )
            )
            if scoped_ids:
                statement = statement.where(PaperRecord.id.in_(scoped_ids))
            if year_from is not None:
                statement = statement.where(or_(PaperRecord.year.is_(None), PaperRecord.year >= year_from))
            if year_to is not None:
                statement = statement.where(or_(PaperRecord.year.is_(None), PaperRecord.year <= year_to))
            rows = session.execute(
                statement.order_by(
                    lexical_score.desc(), PaperRecord.created_at.desc(),
                    ChunkRecord.page, ChunkRecord.id,
                ).limit(pool_size)
            ).all()

            grouped: dict[str, tuple[PaperRecord, list[Chunk]]] = {}
            for paper, chunk in rows:
                grouped.setdefault(paper.id, (paper, []))[1].append(Chunk(
                    id=chunk.id, paper_id=chunk.paper_id, page=chunk.page,
                    section=chunk.section, text=chunk.text,
                ))
            return [Paper(
                id=paper.id, user_id=paper.user_id, workspace_id=paper.workspace_id,
                created_by=paper.created_by, title=paper.title,
                authors=list(paper.authors or []), year=paper.year,
                abstract=paper.abstract, source=paper.source,
                external_id=paper.external_id, status=paper.status,
                page_count=paper.page_count, created_at=paper.created_at.isoformat(),
                chunks=chunks, content_hash=paper.content_hash,
                error_message=paper.error_message, storage_key=paper.storage_key,
                mime_type=paper.mime_type, byte_size=paper.byte_size,
            ) for paper, chunks in grouped.values()]

    def list_library_page(self, workspace_id: str, *, page: int = 1, page_size: int = 50, query: str = "", status: str | None = None, source: str | None = None, tag_id: str | None = None, source_set_id: str | None = None, decision: str | None = None) -> PaperLibraryPage:
        page, page_size = max(1, page), max(1, min(page_size, 100))
        with self.session_factory() as session:
            stmt = select(PaperRecord).where(PaperRecord.workspace_id == workspace_id)
            if query.strip():
                pattern = f"%{query.strip()}%"; stmt = stmt.where(or_(PaperRecord.title.ilike(pattern), PaperRecord.abstract.ilike(pattern)))
            if status: stmt = stmt.where(PaperRecord.status == status)
            if source: stmt = stmt.where(PaperRecord.source == source)
            if tag_id: stmt = stmt.where(PaperRecord.id.in_(select(PaperTagRecord.paper_id).where(PaperTagRecord.tag_id == tag_id)))
            if source_set_id:
                source_set = self._scoped_source_set(session, workspace_id, source_set_id)
                stmt = stmt.where(PaperRecord.id.in_(select(SourceSetPaperRecord.paper_id).where(SourceSetPaperRecord.source_set_id == source_set.id)))
            if decision == "undecided": stmt = stmt.outerjoin(PaperDecisionRecord, PaperDecisionRecord.paper_id == PaperRecord.id).where(or_(PaperDecisionRecord.decision.is_(None), PaperDecisionRecord.decision == "undecided"))
            elif decision: stmt = stmt.join(PaperDecisionRecord, PaperDecisionRecord.paper_id == PaperRecord.id).where(PaperDecisionRecord.decision == decision)
            total = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
            page_stmt = stmt.with_only_columns(PaperRecord, func.count(ChunkRecord.id).label("chunk_count")).outerjoin(ChunkRecord, ChunkRecord.paper_id == PaperRecord.id).group_by(PaperRecord.id).order_by(PaperRecord.created_at.desc(), PaperRecord.id).offset((page - 1) * page_size).limit(page_size)
            records = session.execute(page_stmt).all()
            ids = [record.id for record, _ in records]
            tag_map = {paper_id: [] for paper_id in ids}
            for paper_id, current_tag_id in session.execute(select(PaperTagRecord.paper_id, PaperTagRecord.tag_id).where(PaperTagRecord.paper_id.in_(ids))).all() if ids else []: tag_map[paper_id].append(current_tag_id)
            decision_map = {row.paper_id: row for row in session.scalars(select(PaperDecisionRecord).where(PaperDecisionRecord.paper_id.in_(ids))).all()} if ids else {}
            items = []
            for record, chunk_count in records:
                row = decision_map.get(record.id)
                items.append(PaperLibraryItem(id=record.id, title=record.title, authors=list(record.authors or []), year=record.year, abstract=record.abstract, source=record.source, external_id=record.external_id, status=record.status, page_count=record.page_count, chunk_count=int(chunk_count), created_at=record.created_at.isoformat(), error_message=record.error_message, tag_ids=tag_map[record.id], decision=PaperDecision(paper_id=record.id, decision=row.decision if row else "undecided", reason=row.reason if row else "", updated_at=row.updated_at.isoformat() if row else None)))
            def facets(column): return {str(key): int(value) for key, value in session.execute(select(column, func.count()).where(PaperRecord.workspace_id == workspace_id).group_by(column)).all() if key is not None}
            decisions = {key: int(value) for key, value in session.execute(select(PaperDecisionRecord.decision, func.count()).where(PaperDecisionRecord.workspace_id == workspace_id).group_by(PaperDecisionRecord.decision)).all()}
            decisions["undecided"] = decisions.get("undecided", 0) + max(0, int(session.scalar(select(func.count()).select_from(PaperRecord).where(PaperRecord.workspace_id == workspace_id)) or 0) - sum(decisions.values()))
            return PaperLibraryPage(items=items, total=total, page=page, page_size=page_size, facets=PaperLibraryFacets(sources=facets(PaperRecord.source), statuses=facets(PaperRecord.status), decisions=decisions))

    def update_paper_decision(self, workspace_id: str, paper_id: str, *, decision: str, reason: str) -> PaperDecision:
        with self.session_factory.begin() as session:
            if session.scalar(select(PaperRecord.id).where(PaperRecord.workspace_id == workspace_id, PaperRecord.id == paper_id)) is None: raise PaperNotFoundError(paper_id)
            row = session.get(PaperDecisionRecord, paper_id); now = datetime.now(timezone.utc)
            if row is None: row = PaperDecisionRecord(paper_id=paper_id, workspace_id=workspace_id, decision=decision, reason=reason, updated_at=now); session.add(row)
            else: row.decision, row.reason, row.updated_at = decision, reason, now
            return PaperDecision(paper_id=paper_id, decision=row.decision, reason=row.reason, updated_at=now.isoformat())

    def bulk_update_paper_tags(self, workspace_id: str, paper_ids: list[str], tag_ids: list[str], *, operation: str) -> None:
        with self.session_factory.begin() as session:
            selected = self._validate_workspace_paper_ids(session, workspace_id, paper_ids); unique_tags = set(tag_ids)
            valid = set(session.scalars(select(TagRecord.id).where(TagRecord.workspace_id == workspace_id, TagRecord.id.in_(unique_tags))).all())
            if len(valid) != len(unique_tags): raise PaperNotFoundError("tag")
            if operation == "remove": session.execute(delete(PaperTagRecord).where(PaperTagRecord.paper_id.in_(selected), PaperTagRecord.tag_id.in_(valid)))
            else:
                existing = set(session.execute(select(PaperTagRecord.paper_id, PaperTagRecord.tag_id).where(PaperTagRecord.paper_id.in_(selected), PaperTagRecord.tag_id.in_(valid))).all())
                session.add_all(PaperTagRecord(paper_id=paper_id, tag_id=current_tag) for paper_id in selected for current_tag in valid if (paper_id, current_tag) not in existing)

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
            source_ids = session.scalars(select(SourceVersionRecord.id).where(
                SourceVersionRecord.workspace_id == workspace_id,
                SourceVersionRecord.paper_id == paper_id,
            )).all()
            if source_ids:
                source_span_ids = session.scalars(select(SourceSpanRecord.id).where(
                    SourceSpanRecord.workspace_id == workspace_id,
                    SourceSpanRecord.source_version_id.in_(source_ids),
                )).all()
                if source_span_ids:
                    session.execute(update(IdeaRecord).where(
                        IdeaRecord.workspace_id == workspace_id,
                        IdeaRecord.source_span_id.in_(source_span_ids),
                    ).values(source_span_id=None))
                session.execute(delete(SourceSpanRecord).where(
                    SourceSpanRecord.workspace_id == workspace_id,
                    SourceSpanRecord.source_version_id.in_(source_ids),
                ))
                session.execute(delete(SourceVersionRecord).where(
                    SourceVersionRecord.workspace_id == workspace_id,
                    SourceVersionRecord.id.in_(source_ids),
                ))
            session.execute(update(IdeaRecord).where(
                IdeaRecord.workspace_id == workspace_id,
                IdeaRecord.paper_id == paper_id,
            ).values(paper_id=None))
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

    def save_comparison(self, workspace_id: str, user_id: str, name: str, paper_ids: list[str], result: list[dict], *, source_set_id: str | None = None, citation_snapshot: list[dict] | None = None, human_judgment: str = "unreviewed", judgment_reason: str = "") -> SavedComparison:
        if source_set_id:
            with self.session_factory() as session:
                self._scoped_source_set(session, workspace_id, source_set_id)
        record = SavedComparisonRecord(id=str(uuid4()), workspace_id=workspace_id, user_id=user_id, name=name.strip(), paper_ids=paper_ids, result=result, source_set_id=source_set_id, citation_snapshot=citation_snapshot or [], human_judgment=human_judgment, judgment_reason=judgment_reason, created_at=datetime.now(timezone.utc))
        with self.session_factory.begin() as session: session.add(record)
        return SavedComparison(id=record.id, user_id=user_id, name=record.name, paper_ids=paper_ids, result=result, source_set_id=source_set_id, citation_snapshot=record.citation_snapshot, human_judgment=human_judgment, judgment_reason=judgment_reason, created_at=record.created_at.isoformat())

    def list_comparisons(self, workspace_id: str) -> list[SavedComparison]:
        with self.session_factory() as session:
            rows = session.scalars(select(SavedComparisonRecord).where(SavedComparisonRecord.workspace_id == workspace_id).order_by(SavedComparisonRecord.created_at.desc())).all()
            return [SavedComparison(id=r.id, user_id=r.user_id, name=r.name, paper_ids=r.paper_ids, result=r.result, source_set_id=r.source_set_id, citation_snapshot=r.citation_snapshot, human_judgment=r.human_judgment, judgment_reason=r.judgment_reason, created_at=r.created_at.isoformat()) for r in rows]

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
        expected_locator = paper.storage_key or f"paper:{paper.id}"
        existing = session.scalar(select(SourceVersionRecord).where(
            SourceVersionRecord.workspace_id == paper.workspace_id,
            SourceVersionRecord.paper_id == paper.id,
            SourceVersionRecord.kind == "paper",
            SourceVersionRecord.locator == expected_locator,
            SourceVersionRecord.content_hash == paper.content_hash,
        ))
        if existing is not None:
            return
        source = SourceVersionRecord(
            id=str(uuid4()), workspace_id=paper.workspace_id, paper_id=paper.id,
            kind="paper", locator=expected_locator,
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
        excerpt: str = "", evidence_links: list[EvidenceLinkCreate] | None = None,
        default_target_claim: str = "",
    ) -> list[EvidenceRefRecord]:
        if not span_ids and not evidence_links:
            return []
        links = list(evidence_links or [])
        unique_ids = list(dict.fromkeys([*span_ids, *(link.source_span_id for link in links)]))
        spans = session.scalars(select(SourceSpanRecord).where(
            SourceSpanRecord.workspace_id == workspace_id,
            SourceSpanRecord.id.in_(unique_ids),
        )).all()
        if len(spans) != len(unique_ids):
            raise PaperNotFoundError("source span")
        spans_by_id = {span.id: span for span in spans}
        now = datetime.now(timezone.utc)
        records: list[EvidenceRefRecord] = []
        # Legacy callers keep working and now persist a complete, exact quote.
        for span_id in dict.fromkeys(span_ids):
            span = spans_by_id[span_id]
            records.append(EvidenceRefRecord(
                id=str(uuid4()), workspace_id=workspace_id, source_span_id=span_id,
                knowledge_node_id=knowledge_node_id, knowledge_edge_id=knowledge_edge_id,
                source_version_id=span.source_version_id, target_claim=default_target_claim,
                role="supports", extraction_quality="unknown", quote_start=0,
                quote_end=len(span.text), verbatim_quote=span.text, excerpt=excerpt, created_at=now,
            ))
        for link in links:
            span = spans_by_id[link.source_span_id]
            quote_start = 0 if link.quote_start is None else link.quote_start
            quote_end = len(span.text) if link.quote_end is None else link.quote_end
            expected_quote = span.text[quote_start:quote_end]
            verbatim_quote = expected_quote if link.quote_start is None and not link.verbatim_quote else link.verbatim_quote
            if quote_start > len(span.text) or quote_end > len(span.text) or verbatim_quote != expected_quote:
                raise ValueError("verbatim_quote must exactly match the selected source span offsets")
            records.append(EvidenceRefRecord(
                id=str(uuid4()), workspace_id=workspace_id, source_span_id=link.source_span_id,
                knowledge_node_id=knowledge_node_id, knowledge_edge_id=knowledge_edge_id,
                source_version_id=span.source_version_id, target_claim=link.target_claim or default_target_claim,
                role=link.role, extraction_quality=link.extraction_quality,
                quote_start=quote_start, quote_end=quote_end, verbatim_quote=verbatim_quote,
                excerpt=excerpt, created_at=now,
            ))
        session.add_all(records)
        return records

    @staticmethod
    def _node_evidence_map(
        session: Session, node_ids: list[str], *, limit: int | None = None,
    ) -> dict[str, list[EvidenceRefRecord]]:
        if not node_ids:
            return {}
        statement = select(EvidenceRefRecord).where(
            EvidenceRefRecord.knowledge_node_id.in_(node_ids)
        ).order_by(EvidenceRefRecord.created_at, EvidenceRefRecord.id)
        if limit is not None:
            statement = statement.limit(max(1, limit))
        rows = session.scalars(statement).all()
        grouped: dict[str, list[EvidenceRefRecord]] = {node_id: [] for node_id in node_ids}
        for row in rows:
            if row.knowledge_node_id:
                grouped.setdefault(row.knowledge_node_id, []).append(row)
        return grouped

    @staticmethod
    def _edge_evidence_map(
        session: Session, edge_ids: list[str], *, limit: int | None = None,
    ) -> dict[str, list[EvidenceRefRecord]]:
        if not edge_ids:
            return {}
        statement = select(EvidenceRefRecord).where(
            EvidenceRefRecord.knowledge_edge_id.in_(edge_ids)
        ).order_by(EvidenceRefRecord.created_at, EvidenceRefRecord.id)
        if limit is not None:
            statement = statement.limit(max(1, limit))
        rows = session.scalars(statement).all()
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
        if paper_id is None and kind.strip().casefold() == "paper":
            raise ValueError("paper source versions are reserved for the ingestion pipeline")
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            if paper_id is not None:
                paper = session.scalar(select(PaperRecord).where(
                    PaperRecord.id == paper_id, PaperRecord.workspace_id == workspace_id,
                ))
                if paper is None:
                    raise PaperNotFoundError(paper_id)
                expected_locator = paper.storage_key or f"paper:{paper.id}"
                if kind.strip() != "paper" or content_hash.lower() != (paper.content_hash or "").lower():
                    raise ValueError("paper source kind and content_hash must match the immutable paper")
                if locator.strip() != expected_locator:
                    raise ValueError("paper source locator must match the immutable paper storage key")
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
        incoming_span_set_hash = _source_span_set_hash(spans)
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
                persisted_span_set_hash = _source_span_set_hash([{
                    "page": item.page, "line_start": item.line_start, "line_end": item.line_end,
                    "char_start": item.char_start, "char_end": item.char_end, "bbox": item.bbox,
                    "cell": item.cell, "locator": item.locator_json, "text": item.text,
                } for item in existing_spans])
                recorded_span_set_hash = (existing.metadata_json or {}).get("span_set_hash")
                if persisted_span_set_hash != incoming_span_set_hash or (
                    recorded_span_set_hash is not None and recorded_span_set_hash != persisted_span_set_hash
                ):
                    raise ResourceConflictError("source version already exists with a different immutable span set")
                return _source_version_model(existing), [_source_span_model(row) for row in existing_spans]

            source_metadata = dict(metadata or {})
            source_metadata["span_set_hash"] = incoming_span_set_hash
            source = SourceVersionRecord(
                id=str(uuid4()), workspace_id=workspace_id, paper_id=None,
                kind=normalized_kind, locator=normalized_locator, content_hash=content_hash,
                metadata_json=source_metadata, created_at=datetime.now(timezone.utc),
            )
            session.add(source)
            # SourceSpan has only the scalar source_version_id, not an ORM
            # relationship.  Flush the parent explicitly before adding spans so
            # PostgreSQL always observes the immutable source first.
            session.flush()
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

    def get_source_span(self, workspace_id: str, source_span_id: str) -> SourceSpan:
        with self.session_factory() as session:
            return _source_span_model(self._scoped_span(session, workspace_id, source_span_id))

    def get_source_materials(
        self, workspace_id: str, source_version_ids: list[str], source_span_ids: list[str],
    ) -> tuple[dict[str, SourceVersion], dict[str, SourceSpan]]:
        """Resolve graph provenance in two bounded queries instead of per hit."""
        version_ids = list(dict.fromkeys(source_version_ids))
        span_ids = list(dict.fromkeys(source_span_ids))
        with self.session_factory() as session:
            versions = session.scalars(select(SourceVersionRecord).where(
                SourceVersionRecord.workspace_id == workspace_id,
                SourceVersionRecord.id.in_(version_ids),
            )).all() if version_ids else []
            spans = session.scalars(select(SourceSpanRecord).where(
                SourceSpanRecord.workspace_id == workspace_id,
                SourceSpanRecord.id.in_(span_ids),
            )).all() if span_ids else []
            return (
                {row.id: _source_version_model(row) for row in versions},
                {row.id: _source_span_model(row) for row in spans},
            )

    def load_scoped_graph_papers(
        self, workspace_id: str, paper_pages: dict[str, set[int | None]], *,
        paper_ids: list[str] | None = None, year_from: int | None = None,
        year_to: int | None = None, chunk_limit: int = 200,
    ) -> list[Paper]:
        """Load exact-page chunks for bounded graph hits, independent of lexical RAG candidates."""
        requested = list(paper_pages)[:200]
        allowed = list(dict.fromkeys(paper_ids or []))[:500]
        if allowed:
            requested = [paper_id for paper_id in requested if paper_id in set(allowed)]
        if not requested:
            return []
        page_clauses = []
        for paper_id in requested:
            pages = {page for page in paper_pages.get(paper_id, set()) if page is not None}
            page_clauses.append(and_(
                ChunkRecord.paper_id == paper_id,
                ChunkRecord.page.in_(list(pages)[:32]) if pages else ChunkRecord.id.is_not(None),
            ))
        with self.session_factory() as session:
            statement = select(PaperRecord, ChunkRecord).join(
                ChunkRecord, ChunkRecord.paper_id == PaperRecord.id,
            ).where(
                PaperRecord.workspace_id == workspace_id,
                PaperRecord.status == "ready",
                PaperRecord.id.in_(requested),
                or_(*page_clauses),
            )
            if year_from is not None:
                statement = statement.where(or_(PaperRecord.year.is_(None), PaperRecord.year >= year_from))
            if year_to is not None:
                statement = statement.where(or_(PaperRecord.year.is_(None), PaperRecord.year <= year_to))
            rows = session.execute(statement.order_by(
                PaperRecord.id, ChunkRecord.page, ChunkRecord.id,
            ).limit(max(1, min(chunk_limit, 200)))).all()
            grouped: dict[str, tuple[PaperRecord, list[Chunk]]] = {}
            for paper, chunk in rows:
                grouped.setdefault(paper.id, (paper, []))[1].append(Chunk(
                    id=chunk.id, paper_id=chunk.paper_id, page=chunk.page,
                    section=chunk.section, text=chunk.text,
                ))
            return [Paper(
                id=paper.id, user_id=paper.user_id, workspace_id=paper.workspace_id,
                created_by=paper.created_by, title=paper.title,
                authors=list(paper.authors or []), year=paper.year,
                abstract=paper.abstract, source=paper.source,
                external_id=paper.external_id, status=paper.status,
                page_count=paper.page_count, created_at=paper.created_at.isoformat(),
                chunks=chunks, content_hash=paper.content_hash,
                error_message=paper.error_message, storage_key=paper.storage_key,
                mime_type=paper.mime_type, byte_size=paper.byte_size,
            ) for paper, chunks in grouped.values()]

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
        evidence_links: list[EvidenceLinkCreate] | None = None,
    ) -> KnowledgeNode:
        status = {"draft": "review_pending", "validated": "verified"}.get(status, status)
        evidence_span_ids = evidence_span_ids or []
        if node_type == "source" and not evidence_span_ids and not evidence_links:
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
                excerpt=evidence_excerpt, evidence_links=evidence_links,
                default_target_claim=record.content,
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

    def retrieve_knowledge_subgraph(
        self, workspace_id: str, query: str, *, seed_limit: int = 12,
        edge_limit: int = 200, evidence_limit: int = 400,
    ) -> tuple[list[KnowledgeNode], list[KnowledgeEdge]]:
        """Return a hard-bounded two-hop graph slice for request-time retrieval."""
        seed_limit = max(1, min(seed_limit, 32))
        edge_limit = max(1, min(edge_limit, 200))
        evidence_limit = max(1, min(evidence_limit, 400))
        terms = list(dict.fromkeys(
            re.findall(r"[a-z0-9][a-z0-9_-]+|[一-龯ぁ-んァ-ヶ]{2,}", query.casefold())
        ))[:12]
        escaped_terms = [
            term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            for term in terms
        ]
        with self.session_factory() as session:
            self._require_workspace(session, workspace_id)
            content_searchable = func.lower(KnowledgeNodeRecord.content)
            content_score = sum((
                case((content_searchable.like(f"%{term}%", escape="\\"), 1), else_=0)
                for term in escaped_terms
            ), start=case((KnowledgeNodeRecord.id.is_not(None), 0), else_=0))
            evidence_seed_ids: list[str] = []
            if escaped_terms:
                evidence_searchable = func.lower(
                    EvidenceRefRecord.target_claim + " " + EvidenceRefRecord.verbatim_quote
                )
                evidence_score = sum((
                    case((evidence_searchable.like(f"%{term}%", escape="\\"), 1), else_=0)
                    for term in escaped_terms
                ), start=case((EvidenceRefRecord.id.is_not(None), 0), else_=0))
                evidence_seed_ids = list(session.scalars(
                    select(KnowledgeNodeRecord.id)
                    .join(EvidenceRefRecord, EvidenceRefRecord.knowledge_node_id == KnowledgeNodeRecord.id)
                    .where(
                        KnowledgeNodeRecord.workspace_id == workspace_id,
                        KnowledgeNodeRecord.status.in_({"active", "verified"}),
                        evidence_score > 0,
                    )
                    .group_by(KnowledgeNodeRecord.id)
                    .order_by(func.max(evidence_score).desc(), KnowledgeNodeRecord.id)
                    .limit(seed_limit)
                ).all())
            remaining_seed_count = seed_limit - len(evidence_seed_ids)
            content_statement = select(KnowledgeNodeRecord.id).where(
                KnowledgeNodeRecord.workspace_id == workspace_id,
                KnowledgeNodeRecord.status.in_({"active", "verified"}),
            )
            if evidence_seed_ids:
                content_statement = content_statement.where(
                    KnowledgeNodeRecord.id.not_in(evidence_seed_ids)
                )
            content_seed_ids = list(session.scalars(
                content_statement.order_by(content_score.desc(), KnowledgeNodeRecord.id)
                .limit(max(0, remaining_seed_count))
            ).all()) if remaining_seed_count else []
            seed_ids = [*evidence_seed_ids, *content_seed_ids]
            if not seed_ids:
                return [], []

            edge_base = select(KnowledgeEdgeRecord).where(
                KnowledgeEdgeRecord.workspace_id == workspace_id,
                KnowledgeEdgeRecord.status.in_({"active", "verified"}),
            )
            first = session.scalars(edge_base.where(or_(
                KnowledgeEdgeRecord.source_node_id.in_(seed_ids),
                KnowledgeEdgeRecord.target_node_id.in_(seed_ids),
            )).order_by(KnowledgeEdgeRecord.id).limit(edge_limit)).all()
            frontier = list(dict.fromkeys(
                node_id for row in first
                for node_id in (row.source_node_id, row.target_node_id)
                if node_id not in seed_ids
            ))[:edge_limit]
            remaining = edge_limit - len(first)
            second = session.scalars(edge_base.where(
                KnowledgeEdgeRecord.source_node_id.in_(frontier)
            ).order_by(KnowledgeEdgeRecord.id).limit(remaining)).all() if frontier and remaining else []
            edges_by_id = {row.id: row for row in [*first, *second]}
            edge_rows = list(edges_by_id.values())[:edge_limit]
            node_ids = list(dict.fromkeys([
                *seed_ids,
                *(node_id for row in edge_rows for node_id in (row.source_node_id, row.target_node_id)),
            ]))[: seed_limit + edge_limit * 2]
            node_rows = session.scalars(select(KnowledgeNodeRecord).where(
                KnowledgeNodeRecord.workspace_id == workspace_id,
                KnowledgeNodeRecord.id.in_(node_ids),
                KnowledgeNodeRecord.status.in_({"active", "verified"}),
            )).all()
            # Edge evidence (especially contradictions) gets a reserved first
            # share, then nodes use the remainder. This is one shared budget,
            # not two independent limits that could hydrate 2x the contract.
            edge_budget = max(1, evidence_limit // 2)
            contradiction_ids = [row.id for row in edge_rows if row.relation == "contradicts"]
            other_edge_ids = [row.id for row in edge_rows if row.relation != "contradicts"]
            edge_evidence = self._edge_evidence_map(
                session, contradiction_ids, limit=edge_budget,
            ) if contradiction_ids else {}
            contradiction_count = sum(len(items) for items in edge_evidence.values())
            if other_edge_ids and contradiction_count < edge_budget:
                other_evidence = self._edge_evidence_map(
                    session, other_edge_ids, limit=edge_budget - contradiction_count,
                )
                edge_evidence.update(other_evidence)
            loaded_edge_evidence = sum(len(items) for items in edge_evidence.values())
            remaining_evidence = evidence_limit - loaded_edge_evidence
            node_evidence = (
                self._node_evidence_map(session, node_ids, limit=remaining_evidence)
                if remaining_evidence > 0 else {node_id: [] for node_id in node_ids}
            )
            return (
                [_knowledge_node_model(row, node_evidence.get(row.id, [])) for row in node_rows],
                [_knowledge_edge_model(row, edge_evidence.get(row.id, [])) for row in edge_rows],
            )

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

    @staticmethod
    def _dependent_node_ids(
        session: Session, workspace_id: str, premise_ids: set[str],
    ) -> set[str]:
        """Return nodes whose validity depends on the supplied premises.

        Most relations point from premise to derived node. ``extends``,
        ``depends_on`` and ``implements`` express the dependency in the
        opposite direction (the source depends on the target).
        Contradiction and generic relation edges never trigger invalidation.
        """
        forward = session.scalars(select(KnowledgeEdgeRecord.target_node_id).where(
            KnowledgeEdgeRecord.workspace_id == workspace_id,
            KnowledgeEdgeRecord.source_node_id.in_(premise_ids),
            KnowledgeEdgeRecord.status.in_({"active", "verified"}),
            KnowledgeEdgeRecord.relation.in_(_FORWARD_DEPENDENCY_RELATIONS),
        )).all()
        reverse = session.scalars(select(KnowledgeEdgeRecord.source_node_id).where(
            KnowledgeEdgeRecord.workspace_id == workspace_id,
            KnowledgeEdgeRecord.target_node_id.in_(premise_ids),
            KnowledgeEdgeRecord.status.in_({"active", "verified"}),
            KnowledgeEdgeRecord.relation.in_(_REVERSE_DEPENDENCY_RELATIONS),
        )).all()
        return set(forward) | set(reverse)

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
                targets = self._dependent_node_ids(session, workspace_id, frontier)
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
                    targets = self._dependent_node_ids(session, workspace_id, frontier)
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
        evidence_excerpt: str = "", created_by: str | None = None,
        status: str = "active", origin: str = "manual",
        evidence_links: list[EvidenceLinkCreate] | None = None,
    ) -> KnowledgeEdge:
        if source_node_id == target_node_id:
            raise ValueError("knowledge edges must connect distinct nodes")
        normalized_relation = relation.strip().casefold()
        if normalized_relation not in _KNOWLEDGE_RELATIONS or (not evidence_span_ids and not evidence_links):
            raise ValueError("knowledge edges require a relation and at least one evidence span")
        if status not in _KNOWLEDGE_STATUSES or origin not in {"manual", "llm", "import"}:
            raise ValueError("invalid knowledge edge status or origin")
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            if created_by is not None and session.get(UserRecord, created_by) is None:
                raise PaperNotFoundError(created_by)
            self._scoped_node(session, workspace_id, source_node_id)
            self._scoped_node(session, workspace_id, target_node_id)
            now = datetime.now(timezone.utc)
            record = KnowledgeEdgeRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                source_node_id=source_node_id, target_node_id=target_node_id,
                relation=normalized_relation, status=status, origin=origin,
                metadata_json=dict(metadata or {}), created_at=now, updated_at=now,
            )
            session.add(record)
            session.flush()
            evidence = self._add_evidence_refs(
                session, workspace_id, evidence_span_ids, knowledge_edge_id=record.id,
                excerpt=evidence_excerpt, evidence_links=evidence_links,
                default_target_claim=self._scoped_node(session, workspace_id, target_node_id).content,
            )
            return _knowledge_edge_model(record, evidence)

    def set_knowledge_edge_status(
        self, workspace_id: str, edge_id: str, *, status: str,
        actor_id: str | None, reason: str,
    ) -> KnowledgeEdge:
        if status not in _KNOWLEDGE_STATUSES or not reason.strip():
            raise ValueError("valid edge status and transition reason are required")
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            edge = session.scalar(select(KnowledgeEdgeRecord).where(
                KnowledgeEdgeRecord.workspace_id == workspace_id,
                KnowledgeEdgeRecord.id == edge_id,
            ))
            if edge is None:
                raise PaperNotFoundError(edge_id)
            previous, now = edge.status, datetime.now(timezone.utc)
            if previous != status:
                session.add(KnowledgeEdgeStatusEventRecord(
                    id=str(uuid4()), workspace_id=workspace_id, knowledge_edge_id=edge.id,
                    actor_id=actor_id, from_status=previous, to_status=status,
                    reason=reason.strip(), created_at=now,
                ))
                edge.status, edge.updated_at = status, now
            evidence = self._edge_evidence_map(session, [edge.id]).get(edge.id, [])
            return _knowledge_edge_model(edge, evidence)

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
            self._lock_reasoning_lineage(session, workspace_id)
            node_ids = list(dict.fromkeys(input_node_ids + output_node_ids))
            if node_ids:
                rows = session.scalars(select(KnowledgeNodeRecord.id).where(
                    KnowledgeNodeRecord.workspace_id == workspace_id,
                    KnowledgeNodeRecord.id.in_(node_ids),
                )).all()
                if len(rows) != len(node_ids):
                    raise PaperNotFoundError("knowledge node")
            self._assert_reasoning_lineage_acyclic(
                session, workspace_id, input_node_ids, output_node_ids,
            )
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

    def forward_propagate_hypothesis(
        self, workspace_id: str, *, input_node_ids: list[str], hypothesis_content: str,
        evidence_span_ids: list[str], evidence_excerpt: str = "", prompt: str = "",
        operator: str = "formulate_hypothesis", metadata: dict | None = None,
        confidence: float | None = None, phase: str = "hypothesis_generation",
        created_by: str | None = None, evidence_links: list[EvidenceLinkCreate] | None = None,
    ) -> ForwardPropagationResult:
        """Atomically persist a reviewable hypothesis and its full provenance.

        An immutable reasoning run records the selected inputs and generated
        output. Every input-to-hypothesis edge is LLM-originated, pending human
        review, and separately grounded in the supplied immutable source spans.
        """
        input_ids = list(dict.fromkeys(input_node_ids))
        span_ids = list(dict.fromkeys([*evidence_span_ids, *((link.source_span_id) for link in evidence_links or [])]))
        if not input_ids:
            raise ValueError("at least one input knowledge node is required")
        if not hypothesis_content.strip():
            raise ValueError("hypothesis content is required")
        if not span_ids:
            raise ValueError("forward propagation requires at least one evidence span")
        if not operator.strip():
            raise ValueError("reasoning operator is required")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            self._require_workspace(session, workspace_id)
            self._lock_reasoning_lineage(session, workspace_id)
            if created_by is not None and session.get(UserRecord, created_by) is None:
                raise PaperNotFoundError(created_by)
            input_records = session.scalars(select(KnowledgeNodeRecord).where(
                KnowledgeNodeRecord.workspace_id == workspace_id,
                KnowledgeNodeRecord.id.in_(input_ids),
            )).all()
            if len(input_records) != len(input_ids):
                raise PaperNotFoundError("knowledge node")
            found_spans = session.scalars(select(SourceSpanRecord.id).where(
                SourceSpanRecord.workspace_id == workspace_id,
                SourceSpanRecord.id.in_(span_ids),
            )).all()
            if len(found_spans) != len(span_ids):
                raise PaperNotFoundError("source span")

            hypothesis = KnowledgeNodeRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                node_type="hypothesis", status="review_pending",
                layer=max(record.layer for record in input_records) + 1,
                content=hypothesis_content.strip(), phase=phase.strip() or "hypothesis_generation",
                confidence=confidence, metadata_json=dict(metadata or {}),
                created_at=now, updated_at=now,
            )
            session.add(hypothesis)

            run = ReasoningRunRecord(
                id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                operator=operator.strip()[:64], status="succeeded", prompt=prompt,
                metadata_json=dict(metadata or {}), created_at=now, updated_at=now,
            )
            session.add(run)
            hypothesis.metadata_json = {**dict(metadata or {}), "reasoning_run_id": run.id}
            run_inputs = [ReasoningRunInputRecord(
                id=str(uuid4()), reasoning_run_id=run.id, knowledge_node_id=node_id, ordinal=index,
            ) for index, node_id in enumerate(input_ids, start=1)]
            run_outputs = [ReasoningRunOutputRecord(
                id=str(uuid4()), reasoning_run_id=run.id, knowledge_node_id=hypothesis.id, ordinal=1,
            )]
            session.add_all(run_inputs + run_outputs)

            edges: list[KnowledgeEdgeRecord] = []
            for node_id in input_ids:
                edge = KnowledgeEdgeRecord(
                    id=str(uuid4()), workspace_id=workspace_id, created_by=created_by,
                    source_node_id=node_id, target_node_id=hypothesis.id,
                    relation="formulates", status="review_pending", origin="llm",
                    metadata_json={**dict(metadata or {}), "reasoning_run_id": run.id},
                    created_at=now, updated_at=now,
                )
                session.add(edge)
                edges.append(edge)
            session.flush()
            node_evidence = self._add_evidence_refs(
                session, workspace_id, evidence_span_ids, knowledge_node_id=hypothesis.id,
                excerpt=evidence_excerpt, evidence_links=evidence_links,
                default_target_claim=hypothesis.content,
            )
            edge_evidence = {
                edge.id: self._add_evidence_refs(
                    session, workspace_id, evidence_span_ids, knowledge_edge_id=edge.id,
                    excerpt=evidence_excerpt, evidence_links=evidence_links,
                    default_target_claim=hypothesis.content,
                )
                for edge in edges
            }
            return ForwardPropagationResult(
                hypothesis=_knowledge_node_model(hypothesis, node_evidence),
                edges=[_knowledge_edge_model(edge, edge_evidence[edge.id]) for edge in edges],
                reasoning_run=_reasoning_run_model(run, run_inputs, run_outputs),
            )

    @staticmethod
    def _lock_reasoning_lineage(session: Session, workspace_id: str) -> None:
        """Serialize lineage validation and writes within one workspace.

        PostgreSQL honors ``FOR UPDATE`` on the workspace row, so concurrent
        A→B and B→A runs cannot both validate against stale lineage. SQLite
        ignores the clause and retains its normal database-writer serialization.
        """
        locked_workspace = session.scalar(
            select(WorkspaceRecord.id)
            .where(WorkspaceRecord.id == workspace_id)
            .with_for_update()
        )
        if locked_workspace is None:
            raise PaperNotFoundError(workspace_id)

    @staticmethod
    def _assert_reasoning_lineage_acyclic(
        session: Session, workspace_id: str, input_node_ids: list[str],
        output_node_ids: list[str], *, exclude_run_id: str | None = None,
    ) -> None:
        """Reject self-supporting or transitive cycles in immutable run lineage."""
        input_ids, output_ids = set(input_node_ids), set(output_node_ids)
        if input_ids & output_ids:
            raise ValueError("reasoning run inputs and outputs must be disjoint")
        if not input_ids or not output_ids:
            return
        statement = (
            select(
                ReasoningRunInputRecord.knowledge_node_id,
                ReasoningRunOutputRecord.knowledge_node_id,
            )
            .join(
                ReasoningRunOutputRecord,
                ReasoningRunOutputRecord.reasoning_run_id
                == ReasoningRunInputRecord.reasoning_run_id,
            )
            .join(
                ReasoningRunRecord,
                ReasoningRunRecord.id == ReasoningRunInputRecord.reasoning_run_id,
            )
            .where(ReasoningRunRecord.workspace_id == workspace_id)
        )
        if exclude_run_id is not None:
            statement = statement.where(ReasoningRunRecord.id != exclude_run_id)
        adjacency: dict[str, set[str]] = {}
        for source_id, target_id in session.execute(statement).all():
            adjacency.setdefault(source_id, set()).add(target_id)

        for output_id in output_ids:
            frontier, visited = [output_id], set()
            while frontier:
                current = frontier.pop()
                if current in input_ids:
                    raise ValueError("reasoning run would create a provenance cycle")
                if current in visited:
                    continue
                visited.add(current)
                frontier.extend(adjacency.get(current, ()))

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
            self._require_workspace(session, workspace_id)
            self._lock_reasoning_lineage(session, workspace_id)
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
                input_ids = session.scalars(select(ReasoningRunInputRecord.knowledge_node_id).where(
                    ReasoningRunInputRecord.reasoning_run_id == reasoning_run_id,
                )).all()
                self._assert_reasoning_lineage_acyclic(
                    session, workspace_id, list(input_ids), unique_ids,
                    exclude_run_id=reasoning_run_id,
                )
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
