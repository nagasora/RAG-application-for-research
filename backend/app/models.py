from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class PaperDecision(BaseModel):
    paper_id: str
    decision: Literal["undecided", "included", "excluded"] = "undecided"
    reason: str = ""
    updated_at: str | None = None


class PaperDecisionUpdate(BaseModel):
    decision: Literal["undecided", "included", "excluded"]
    reason: str = Field(default="", max_length=10_000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class PaperTagsBulkUpdate(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=500)
    tag_ids: list[str] = Field(default_factory=list, max_length=100)
    operation: Literal["add", "remove"] = "add"


class PaperLibraryItem(PaperSummary):
    tag_ids: list[str] = Field(default_factory=list)
    decision: PaperDecision


class PaperLibraryFacets(BaseModel):
    sources: dict[str, int] = Field(default_factory=dict)
    statuses: dict[str, int] = Field(default_factory=dict)
    tags: dict[str, int] = Field(default_factory=dict)
    decisions: dict[str, int] = Field(default_factory=dict)


class PaperLibraryPage(BaseModel):
    items: list[PaperLibraryItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    facets: PaperLibraryFacets


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


class SourceVersion(BaseModel):
    id: str
    workspace_id: str
    paper_id: str | None = None
    kind: str
    locator: str
    content_hash: str
    metadata: dict = Field(default_factory=dict)
    created_at: str
    # The locator is an immutable storage/audit identity.  Keep it, but give
    # source pickers a human-readable label when the source is a paper.
    paper_title: str | None = None
    display_name: str | None = None


class SourceSpan(BaseModel):
    id: str
    workspace_id: str
    source_version_id: str
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    bbox: list[float] | None = None
    cell: dict | list | None = None
    locator: dict = Field(default_factory=dict)
    text: str = ""
    created_at: str


class EvidenceRef(BaseModel):
    id: str
    workspace_id: str
    source_span_id: str
    knowledge_node_id: str | None = None
    knowledge_edge_id: str | None = None
    # Immutable provenance captured at link creation.  These duplicate the
    # span's version deliberately: a link remains resolvable after a source is
    # re-imported as a newer version.
    source_version_id: str
    target_claim: str = ""
    role: Literal["supports", "contradicts", "context", "mentions"] = "supports"
    extraction_quality: Literal["high", "medium", "low", "unknown"] = "unknown"
    quote_start: int = Field(ge=0)
    quote_end: int = Field(ge=0)
    verbatim_quote: str = ""
    excerpt: str = ""
    created_at: str


class KnowledgeNode(BaseModel):
    id: str
    workspace_id: str
    created_by: str | None = None
    node_type: Literal["source", "idea", "constraint", "hypothesis", "experiment"]
    status: Literal["review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"]
    layer: int = Field(ge=0)
    content: str
    phase: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    created_at: str
    updated_at: str


class HypothesisCardCreate(BaseModel):
    claim: str = Field(min_length=1, max_length=20_000)
    mechanism: str = ""
    target: str = ""
    conditions: str = ""
    intervention: str = ""
    outcome: str = ""
    direction: str = ""
    assumptions: list[str] = Field(default_factory=list)
    competing_theories: list[str] = Field(default_factory=list)
    predictions: list[str] = Field(default_factory=list)
    falsifiers: list[str] = Field(default_factory=list)
    test: str = ""


class HypothesisCard(HypothesisCardCreate):
    id: str
    workspace_id: str
    created_by: str | None = None
    status: Literal["draft", "reviewable", "reviewed", "supported", "rejected"] = "draft"
    human_reviewed: bool = False
    empirically_supported: bool = False
    metadata: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class HypothesisCardStatusUpdate(BaseModel):
    status: Literal["draft", "reviewable", "reviewed", "supported", "rejected"]
    human_reviewed: bool | None = None
    empirically_supported: bool | None = None


class DiscoveryItemCreate(BaseModel):
    provider: Literal["semantic_scholar"] = "semantic_scholar"
    provider_paper_id: str = Field(min_length=1, max_length=256)
    classification: Literal["supports", "contradicts", "boundary_condition", "method_alternative", "duplicate"]
    title: str = Field(min_length=1, max_length=20_000)
    abstract: str = ""
    source_quote: str = ""
    source_url: str = ""
    license: str = "unknown"
    rate_limit_policy: str = "api_key_intro_1_rps"
    snapshot: dict = Field(default_factory=dict)


class DiscoveryItem(DiscoveryItemCreate):
    id: str
    workspace_id: str
    created_by: str | None = None
    review_status: Literal["pending", "accepted", "rejected"] = "pending"
    fetched_at: str
    created_at: str


class DiscoveryReviewUpdate(BaseModel):
    review_status: Literal["accepted", "rejected"]


class BeliefEventCreate(BaseModel):
    belief_key: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20_000)
    status: Literal["proposed", "supported", "disputed", "rejected", "superseded"]
    reason: str = ""
    hypothesis_card_id: str | None = None
    reasoning_run_id: str | None = None


class BeliefEvent(BeliefEventCreate):
    id: str
    workspace_id: str
    created_by: str | None = None
    created_at: str


class ExperimentPlanCreate(BaseModel):
    hypothesis_card_id: str | None = None
    intervention: str = Field(min_length=1); measurement: str = Field(min_length=1); controls: str = Field(min_length=1)
    confounders: list[str] = Field(default_factory=list); predictions: list[str] = Field(default_factory=list)
    decision_threshold: str = Field(min_length=1); stopping_rule: str = Field(min_length=1); required_data: str = Field(min_length=1)
    cost: str = Field(min_length=1); competing_hypothesis_discrimination: str = Field(min_length=1); evidence: list[str] = Field(default_factory=list)


class ExperimentPlan(ExperimentPlanCreate):
    id: str; workspace_id: str; created_by: str | None = None; hypothesis_snapshot: dict | None = None; results: list[dict] = Field(default_factory=list); history: list[dict] = Field(default_factory=list); created_at: str; updated_at: str
class ExperimentResultCreate(BaseModel):
    outcome: str = Field(min_length=1)
    data_snapshot: dict = Field(default_factory=dict)
    interpretation: str = ""


class ExperimentPlanSnapshot(BaseModel):
    schema_version: Literal["paperpilot.experiment-plan.v1"] = "paperpilot.experiment-plan.v1"
    exported_at: str
    experiment: ExperimentPlan


class KnowledgeEdge(BaseModel):
    id: str
    workspace_id: str
    source_node_id: str
    target_node_id: str
    created_by: str | None = None
    relation: Literal["informs", "supports", "extends", "formulates", "contradicts", "implements", "depends_on", "related"]
    status: Literal["review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"]
    origin: Literal["manual", "llm", "import"]
    metadata: dict = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ReasoningRunLink(BaseModel):
    knowledge_node_id: str
    ordinal: int


class ReasoningRun(BaseModel):
    id: str
    workspace_id: str
    created_by: str | None = None
    operator: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    prompt: str = ""
    metadata: dict = Field(default_factory=dict)
    inputs: list[ReasoningRunLink] = Field(default_factory=list)
    outputs: list[ReasoningRunLink] = Field(default_factory=list)
    created_at: str
    updated_at: str


class NodeFeedback(BaseModel):
    id: str
    workspace_id: str
    knowledge_node_id: str
    user_id: str
    verdict: Literal["helpful", "not_helpful", "accepted", "rejected"]
    rating: float | None = Field(default=None, ge=-1, le=1)
    comment: str = ""
    created_at: str
    updated_at: str


class CanvasLayout(BaseModel):
    id: str
    workspace_id: str
    canvas_id: str = "default"
    knowledge_node_id: str
    x: float
    y: float
    width: float | None = None
    height: float | None = None
    z_index: int = 0
    collapsed: bool = False
    updated_at: str


class SourceVersionCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    locator: str = Field(min_length=1, max_length=4_000)
    content_hash: str = Field(min_length=64, max_length=64)
    paper_id: str | None = None
    content: str | None = Field(default=None, max_length=10_000_000)
    metadata: dict = Field(default_factory=dict)


class SourceImportCreate(BaseModel):
    kind: Literal["latex", "python", "notebook", "csv", "chat", "markdown"]
    locator: str = Field(min_length=1, max_length=4_000)
    content: str = Field(min_length=1, max_length=5 * 1024 * 1024)
    content_hash: str = Field(min_length=64, max_length=64)
    metadata: dict = Field(default_factory=dict)


class SourceImportResult(BaseModel):
    source: SourceVersion
    spans: list[SourceSpan] = Field(default_factory=list)


class EvidenceLinkCreate(BaseModel):
    """A claim-level, offset-addressable link to one immutable source span."""

    source_span_id: str
    target_claim: str = Field(default="", max_length=100_000)
    role: Literal["supports", "contradicts", "context", "mentions"] = "supports"
    extraction_quality: Literal["high", "medium", "low", "unknown"] = "unknown"
    quote_start: int | None = Field(default=None, ge=0)
    quote_end: int | None = Field(default=None, ge=0)
    verbatim_quote: str = Field(default="", max_length=100_000)

    @model_validator(mode="after")
    def explicit_quote_requires_offsets(self) -> "EvidenceLinkCreate":
        if (self.quote_start is None) != (self.quote_end is None):
            raise ValueError("quote_start and quote_end must be supplied together")
        if self.verbatim_quote and self.quote_start is None:
            raise ValueError("verbatim_quote requires quote_start and quote_end")
        if self.quote_start is not None and self.quote_end is not None and self.quote_end < self.quote_start:
            raise ValueError("quote_end must not precede quote_start")
        return self


class KnowledgeNodeCreate(BaseModel):
    node_type: Literal["source", "idea", "constraint", "hypothesis", "experiment"]
    content: str = Field(min_length=1, max_length=100_000)
    layer: int = Field(default=0, ge=0, le=100)
    status: Literal["review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"] = "review_pending"
    phase: str = Field(default="unclassified", max_length=64)
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict = Field(default_factory=dict)
    evidence_span_ids: list[str] = Field(default_factory=list, max_length=32)
    evidence_excerpt: str = Field(default="", max_length=10_000)
    evidence_links: list[EvidenceLinkCreate] = Field(default_factory=list, max_length=32)


class KnowledgeNodeStatusUpdate(BaseModel):
    status: Literal["review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"]


class KnowledgeNodeStatusResult(BaseModel):
    node: KnowledgeNode
    affected_node_ids: list[str] = Field(default_factory=list)


class KnowledgeEdgeCreate(BaseModel):
    source_node_id: str
    target_node_id: str
    relation: Literal["informs", "supports", "extends", "formulates", "contradicts", "implements", "depends_on", "related"]
    evidence_span_ids: list[str] = Field(default_factory=list, max_length=32)
    metadata: dict = Field(default_factory=dict)
    evidence_excerpt: str = Field(default="", max_length=10_000)
    evidence_links: list[EvidenceLinkCreate] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def requires_evidence(self) -> "KnowledgeEdgeCreate":
        if not self.evidence_span_ids and not self.evidence_links:
            raise ValueError("knowledge edges require at least one evidence span or evidence link")
        return self


class KnowledgeEdgeStatusUpdate(BaseModel):
    status: Literal["review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"]
    reason: str = Field(min_length=1, max_length=10_000)


class ReasoningRunCreate(BaseModel):
    operator: str = Field(min_length=1, max_length=64)
    input_node_ids: list[str] = Field(default_factory=list, max_length=32)
    output_node_ids: list[str] = Field(default_factory=list, max_length=32)
    prompt: str = Field(default="", max_length=20_000)
    metadata: dict = Field(default_factory=dict)


class ForwardPropagationCreate(BaseModel):
    """Create one reviewable hypothesis from selected graph nodes.

    The caller supplies the generated text (normally from an LLM) and immutable
    source spans that ground every newly-created premise edge.
    """

    input_node_ids: list[str] = Field(min_length=1, max_length=32)
    hypothesis_content: str | None = Field(default=None, max_length=100_000)
    evidence_span_ids: list[str] = Field(default_factory=list, max_length=32)
    evidence_excerpt: str = Field(default="", max_length=10_000)
    evidence_links: list[EvidenceLinkCreate] = Field(default_factory=list, max_length=32)
    prompt: str = Field(default="", max_length=20_000)
    operator: str = Field(default="formulate_hypothesis", min_length=1, max_length=64)
    metadata: dict = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    phase: str = Field(default="hypothesis_generation", max_length=64)

    @model_validator(mode="after")
    def requires_evidence(self) -> "ForwardPropagationCreate":
        if not self.evidence_span_ids and not self.evidence_links:
            raise ValueError("forward propagation requires at least one evidence span or evidence link")
        return self


class ForwardPropagationResult(BaseModel):
    hypothesis: KnowledgeNode
    edges: list[KnowledgeEdge] = Field(default_factory=list)
    reasoning_run: ReasoningRun


class NodeFeedbackCreate(BaseModel):
    verdict: Literal["helpful", "not_helpful", "accepted", "rejected"]
    rating: float | None = Field(default=None, ge=-1, le=1)
    comment: str = Field(default="", max_length=10_000)


class CanvasLayoutUpdate(BaseModel):
    x: float
    y: float
    canvas_id: str = Field(default="default", min_length=1, max_length=64)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    z_index: int = Field(default=0, ge=-10_000, le=10_000)
    collapsed: bool = False


class GraphSnapshot(BaseModel):
    nodes: list[KnowledgeNode] = Field(default_factory=list)
    edges: list[KnowledgeEdge] = Field(default_factory=list)
    layouts: list[CanvasLayout] = Field(default_factory=list)


class GraphRetrievalSeed(BaseModel):
    node_id: str
    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(default=1, ge=0, le=1)
    retrieval_reason: str = Field(default="base_retrieval", max_length=500)


class GraphRetrieveRequest(BaseModel):
    seeds: list[GraphRetrievalSeed] = Field(min_length=1, max_length=32)
    top_k: int = Field(default=8, ge=1, le=20)
    max_degree: int = Field(default=12, ge=1, le=50)
    max_first_hop_candidates: int = Field(default=16, ge=1, le=50)


class GraphRetrievalHit(BaseModel):
    node: KnowledgeNode
    score: float
    retrieval_reason: str
    hop_count: int
    hop_path: list[dict] = Field(default_factory=list)


class IngestionJob(BaseModel):
    id: str
    paper_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int
    attempts: int
    error_message: str | None = None
    created_at: str
    updated_at: str


class EmbeddingJobStatus(BaseModel):
    """A provider-safe view of one document embedding job."""
    id: str
    paper_id: str
    provider: Literal["openai", "local"]
    model: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int = Field(ge=0, le=100)
    attempts: int = Field(ge=0)
    total_chunks: int = Field(ge=0)
    completed_chunks: int = Field(ge=0)
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class EmbeddingReindexRequest(BaseModel):
    # Empty means every ready paper in the active workspace.  IDs are checked
    # against that workspace before a job can be created.
    paper_ids: list[str] = Field(default_factory=list, max_length=500)


class EmbeddingReindexResponse(BaseModel):
    provider: Literal["openai", "local"]
    model: str
    mode: Literal["inline", "celery"]
    jobs: list[EmbeddingJobStatus] = Field(default_factory=list)


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


class WorkspaceMember(BaseModel):
    """A workspace member with the identity fields safe to show to collaborators."""

    user: User
    role: Literal["owner", "editor", "viewer"]
    created_at: str


class WorkspaceMemberCreate(BaseModel):
    """Identify an already-provisioned collaborator in the same identity provider."""

    email: str | None = Field(default=None, min_length=3, max_length=320)
    subject: str | None = Field(default=None, min_length=1, max_length=512)
    role: Literal["owner", "editor", "viewer"] = "editor"

    @field_validator("email", "subject")
    @classmethod
    def strip_identity(cls, value: str | None) -> str | None:
        return value.strip() if value else None

    def model_post_init(self, __context: object) -> None:
        if bool(self.email) == bool(self.subject):
            raise ValueError("exactly one of email or subject is required")


class WorkspaceMemberUpdate(BaseModel):
    role: Literal["owner", "editor", "viewer"]


class ResearchQuestion(BaseModel):
    id: str
    workspace_id: str
    created_by: str | None = None
    title: str = ""
    question: str
    created_at: str
    updated_at: str


class ResearchQuestionCreate(BaseModel):
    title: str = Field(default="", max_length=255)
    question: str = Field(min_length=1, max_length=100_000)

    @field_validator("title", "question")
    @classmethod
    def strip_content(cls, value: str) -> str:
        cleaned = value.strip()
        if value and not cleaned:
            raise ValueError("content must not be blank")
        return cleaned


class ResearchQuestionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    question: str | None = Field(default=None, min_length=1, max_length=100_000)

    @field_validator("title", "question")
    @classmethod
    def strip_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if value and not cleaned:
            raise ValueError("content must not be blank")
        return cleaned


class SourceSet(BaseModel):
    id: str
    workspace_id: str
    created_by: str | None = None
    name: str
    description: str = ""
    paper_ids: list[str] = Field(default_factory=list, max_length=500)
    created_at: str
    updated_at: str


class SourceSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10_000)
    paper_ids: list[str] = Field(default_factory=list, max_length=1_000)

    @field_validator("name", "description")
    @classmethod
    def strip_content(cls, value: str) -> str:
        cleaned = value.strip()
        if value and not cleaned:
            raise ValueError("content must not be blank")
        return cleaned


class SourceSetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    paper_ids: list[str] | None = Field(default=None, max_length=1_000)

    @field_validator("name", "description")
    @classmethod
    def strip_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if value and not cleaned:
            raise ValueError("content must not be blank")
        return cleaned


class RunArtifact(BaseModel):
    id: str
    research_run_id: str
    kind: str
    payload: dict | list
    ordinal: int
    created_at: str


class RunArtifactCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    payload: dict | list


class ResearchRun(BaseModel):
    id: str
    workspace_id: str
    created_by: str | None = None
    research_question_id: str | None = None
    source_set_id: str | None = None
    research_question: str = ""
    source_paper_ids: list[str] = Field(default_factory=list)
    excluded_paper_ids: list[str] = Field(default_factory=list)
    purpose: str = ""
    success_criteria: str = ""
    plan: dict | list = Field(default_factory=dict)
    model: str = ""
    prompt_version: str = ""
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    cancel_requested: bool = False
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
    artifacts: list[RunArtifact] = Field(default_factory=list)


class ResearchRunGraphSeed(BaseModel):
    intent: Literal["explore", "challenge", "design"]
    node_id: str = Field(min_length=1, max_length=128)
    # Accepted for rolling client compatibility, but replaced from the
    # workspace-scoped KnowledgeNode at persistence time.
    content: str = ""

    @field_validator("node_id")
    @classmethod
    def normalize_node_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("graph_seed node_id is required")
        return value


class ResearchRunPlan(BaseModel):
    """Free-form run plan with an optional, governed graph seed."""

    model_config = ConfigDict(extra="allow")
    graph_seed: ResearchRunGraphSeed | None = None


class ResearchRunCreate(BaseModel):
    research_question_id: str | None = None
    source_set_id: str | None = None
    source_paper_ids: list[str] = Field(default_factory=list, max_length=1_000)
    excluded_paper_ids: list[str] = Field(default_factory=list, max_length=1_000)
    purpose: str = Field(default="", max_length=20_000)
    success_criteria: str = Field(default="", max_length=20_000)
    plan: ResearchRunPlan | list = Field(default_factory=ResearchRunPlan)
    model: str = Field(default="", max_length=255)
    prompt_version: str = Field(default="", max_length=255)

class IdeaCreate(BaseModel):
    kind: Literal["observation", "interpretation", "hypothesis", "falsifier", "todo"] = "hypothesis"
    content: str = Field(min_length=1, max_length=20_000)
    research_run_id: str | None = None; claim_id: str | None = Field(default=None, max_length=128); paper_id: str | None = None; source_span_id: str | None = None
    checklist: dict = Field(default_factory=dict)

    @field_validator("content", mode="before")
    @classmethod
    def strip_nonempty_content(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("idea content is required")
        return value.strip()

    @field_validator("claim_id")
    @classmethod
    def normalize_claim_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("claim_id cannot be blank")
        return value

    @model_validator(mode="after")
    def claim_requires_research_run(self):
        if self.claim_id and not self.research_run_id:
            raise ValueError("claim_id requires research_run_id")
        return self


class IdeaUpdate(BaseModel):
    # Non-Optional types with an omitted default distinguish PATCH omission
    # from an explicit JSON null, which must be rejected for required fields.
    kind: Literal["observation", "interpretation", "hypothesis", "falsifier", "todo"] = Field(default=None)  # type: ignore[assignment]
    content: str = Field(default=None, min_length=1, max_length=20_000)  # type: ignore[assignment]
    research_run_id: str | None = None
    claim_id: str | None = Field(default=None, max_length=128)
    paper_id: str | None = None
    source_span_id: str | None = None
    checklist: dict | None = None

    @field_validator("content", mode="before")
    @classmethod
    def strip_nonempty_content(cls, value):
        if value is None:
            raise ValueError("idea content cannot be null")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("idea content is required")
        return value.strip()

    @field_validator("claim_id")
    @classmethod
    def normalize_claim_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("claim_id cannot be blank")
        return value

    @model_validator(mode="after")
    def claim_requires_research_run(self):
        # Claim anchors are explicit and self-contained. The store separately
        # validates an existing claim when another PATCH changes its run.
        if self.claim_id and (
            "research_run_id" not in self.model_fields_set or not self.research_run_id
        ):
            raise ValueError("claim_id requires research_run_id")
        return self
class Idea(BaseModel):
    id: str; workspace_id: str; kind: str; content: str; research_run_id: str | None = None; claim_id: str | None = None; paper_id: str | None = None; source_span_id: str | None = None
    checklist: dict = Field(default_factory=dict); status: Literal["unverified", "promoted"] = "unverified"; hypothesis_card_id: str | None = None; created_at: str


class ReviewCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comment body is required")
        return value.strip()


class ReviewComment(BaseModel):
    id: str
    author_id: str | None = None
    body: str
    created_at: str


class ReviewDecisionCreate(BaseModel):
    verdict: Literal["accepted", "rejected", "changes_requested", "needs_more_evidence"]
    reason: str = Field(min_length=1, max_length=20_000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("decision reason is required")
        return value.strip()


class ReviewDecision(BaseModel):
    id: str
    decided_by: str | None = None
    verdict: Literal["accepted", "rejected", "changes_requested", "needs_more_evidence"]
    reason: str
    created_at: str


class ReviewThreadCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    research_run_id: str | None = None
    claim_id: str | None = Field(default=None, max_length=128)
    evidence_link_id: str | None = None
    evidence_snapshot: dict | None = None
    assigned_to: str | None = None

    @model_validator(mode="after")
    def exactly_one_anchor(self):
        if self.claim_id is not None:
            self.claim_id = self.claim_id.strip()
        claim_anchor = bool(self.research_run_id and self.claim_id)
        evidence_anchor = bool(self.evidence_link_id)
        if claim_anchor == evidence_anchor or ((self.research_run_id is None) != (self.claim_id is None)):
            raise ValueError("review thread requires exactly one claim or evidence link anchor")
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("review title is required")
        return self


class ReviewAssignmentUpdate(BaseModel):
    assigned_to: str | None = None


class ReviewThread(BaseModel):
    id: str
    workspace_id: str
    created_by: str | None = None
    title: str
    research_run_id: str | None = None
    claim_id: str | None = None
    claim_artifact_id: str | None = None
    claim_snapshot: dict | None = None
    evidence_link_id: str | None = None
    assigned_to: str | None = None
    status: Literal["open", "resolved"] = "open"
    comments: list[ReviewComment] = Field(default_factory=list)
    decisions: list[ReviewDecision] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ReviewCandidate(BaseModel):
    """A persisted Ask claim that can safely anchor a review thread."""
    research_run_id: str
    claim_id: str
    claim_artifact_id: str
    text: str
    classification: str | None = None
    citation_ids: list[int] = Field(default_factory=list)
    created_at: str


class GraphIdeaCandidate(BaseModel):
    """A selectable, explicitly unverified draft derived from an Ask turn."""
    id: str
    content: str
    kind: Literal["hypothesis", "assumption", "unresolved_question", "planned_test"]
    source_message_id: str
    citation_count: int = Field(ge=0)
    derived_from_memory: bool = False
    classification: Literal["unverified"] = "unverified"


class ConversationGraphDraftCreate(BaseModel):
    """One selected or researcher-authored draft in an atomic graph export."""
    candidate_id: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=100_000)
    kind: Literal["hypothesis", "assumption", "unresolved_question", "planned_test", "manual"]
    derived_from_memory: bool = False

    @field_validator("candidate_id", "content")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("draft identity and content are required")
        return value


class ConversationGraphExportCreate(BaseModel):
    source_span_id: str
    drafts: list[ConversationGraphDraftCreate] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_draft_identities(self):
        ids = [item.candidate_id for item in self.drafts]
        if len(ids) != len(set(ids)):
            raise ValueError("draft candidate_id values must be unique")
        return self


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
    paper_ids: list[str] = Field(default_factory=list, max_length=500)
    year_from: int | None = None
    year_to: int | None = None
    limit: int = Field(default=8, ge=1, le=20)
    conversation_id: str | None = None
    research_run_id: str | None = None
    interaction_mode: Literal["evidence", "synthesis", "explore", "challenge", "design", "update"] = "synthesis"


class Citation(BaseModel):
    index: int
    paper_id: str
    paper_title: str
    chunk_id: str
    page: int
    section: str
    excerpt: str
    score: float
    # Existing paper citations keep the original required fields above.  The
    # optional provenance fields let graph-backed evidence travel through the
    # same API and AgenticRAG pipeline without breaking older clients.
    source_kind: Literal["paper_chunk", "graph_node", "graph_edge"] = "paper_chunk"
    source_version_id: str | None = None
    source_span_id: str | None = None
    evidence_role: Literal["supports", "contradicts", "context", "mentions"] | None = None
    knowledge_node_id: str | None = None
    knowledge_edge_id: str | None = None
    graph_path: list[dict] = Field(default_factory=list)
    retrieval_channels: list[str] = Field(default_factory=list)
    fusion_score: float | None = None
    extraction_quality: Literal["high", "medium", "low", "unknown"] | None = None
    retrieval_reason: str | None = None
    source_quote: str | None = None
    retrieval_stance: Literal["positive", "negative", "neutral"] | None = None


class SearchPreviewResponse(BaseModel):
    """Deterministic, read-only search results that never call an embedding or chat model."""

    citations: list[Citation] = Field(default_factory=list)


class PaperMarkdownSummary(BaseModel):
    """A concise, citation-linked paper summary rendered as Japanese Markdown."""
    paper_id: str
    title: str
    summary: str
    citations: list[Citation] = Field(default_factory=list)
    generation_mode: Literal["llm", "local_fallback"] = "local_fallback"
    model: str | None = None
    fallback_reason: str | None = None


class AnswerClaim(BaseModel):
    claim_id: str
    text: str
    kind: Literal["paper", "general", "hypothesis"]
    citation_ids: list[int] = Field(default_factory=list)
    classification: Literal["evidence_backed", "inference", "general_knowledge", "hypothesis", "unverified"] = "unverified"


class SearchResponse(BaseModel):
    answer: str
    citations: list[Citation]
    conversation_id: str | None = None
    research_run_id: str | None = None
    interaction_mode: Literal["evidence", "synthesis", "explore", "challenge", "design", "update"] = "synthesis"
    draft: bool = False
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


class OperationsStatus(BaseModel):
    ingestion_mode: Literal["inline", "celery"]
    celery_required: bool
    celery_configured: bool
    retry_limit: int
    embedding_retry_limit: int
    backup_restore_runbook: str
    ci017_outbox_note: str
    warnings: list[str] = Field(default_factory=list)


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
    # These fields are populated only for assistant turns written after CI-023.
    # None is intentional: it preserves the unknown status of legacy turns and
    # prevents user messages from being interpreted as synthesized content.
    interaction_mode: Literal["evidence", "synthesis", "explore", "challenge", "design", "update"] | None = None
    draft: bool | None = None
    claims: list[AnswerClaim] = Field(default_factory=list)
    research_run_id: str | None = None
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
    evidence: list[dict] = Field(default_factory=list)
    evidence_status: Literal["grounded", "unresolved"] = "unresolved"
    conditions: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    human_judgment: Literal["accepted", "held", "rejected", "unreviewed"] = "unreviewed"
    judgment_reason: str = ""


class ResearchGap(BaseModel):
    paper_id: str
    paper_title: str
    page: str
    gap: str
    opportunity: str
    gap_type: Literal["author_limitation", "contradiction", "external_validity", "method", "unconnected"] = "author_limitation"
    evidence: list[dict] = Field(default_factory=list)
    evidence_status: Literal["grounded", "unresolved"] = "unresolved"
    conditions: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    human_judgment: Literal["accepted", "held", "rejected", "unreviewed"] = "unreviewed"
    judgment_reason: str = ""


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
    source_set_id: str | None = None
    citation_snapshot: list[dict] = Field(default_factory=list)
    human_judgment: Literal["accepted", "held", "rejected", "unreviewed"] = "unreviewed"
    judgment_reason: str = Field(default="", max_length=10_000)


class SavedComparison(BaseModel):
    id: str
    user_id: str
    name: str
    paper_ids: list[str]
    result: list[dict]
    created_at: str
    source_set_id: str | None = None
    citation_snapshot: list[dict] = Field(default_factory=list)
    human_judgment: Literal["accepted", "held", "rejected", "unreviewed"] = "unreviewed"
    judgment_reason: str = ""
