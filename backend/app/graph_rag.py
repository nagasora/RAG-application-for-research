"""Bounded, provenance-preserving graph expansion for RAG retrieval.

The module deliberately has no database dependency.  A store adapter only has
to implement :class:`GraphNeighborProvider` and must enforce the workspace
filter before returning its directed edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol


DEFAULT_RELATION_PRIORITY: dict[str, float] = {
    "informs": 1.0,
    "supports": 0.98,
    "contradicts": 0.98,
    "extends": 0.92,
    "formulates": 0.90,
    "implements": 0.86,
    "depends_on": 0.84,
    "related": 0.55,
}


@dataclass(frozen=True)
class GraphEdge:
    """A directed, already-authorized edge supplied by a graph repository."""

    id: str
    source_id: str
    target_id: str
    relation: str
    confidence: float


class GraphNeighborProvider(Protocol):
    """Return only outgoing edges visible in ``workspace_id`` for ``node_id``."""

    def outgoing_edges(self, workspace_id: str, node_id: str) -> Iterable[GraphEdge]: ...


@dataclass(frozen=True)
class RetrievalSeed:
    """A lexical/vector candidate before graph expansion."""

    node_id: str
    relevance: float
    confidence: float = 1.0
    retrieval_reason: str = "base_retrieval"


@dataclass(frozen=True)
class HopPathStep:
    edge_id: str
    from_node_id: str
    to_node_id: str
    relation: str
    confidence: float


@dataclass(frozen=True)
class GraphRetrievalHit:
    """A selected source or graph-expanded candidate with an auditable path."""

    node_id: str
    relevance: float
    confidence: float
    path_score: float
    score: float
    hop_count: int
    retrieval_reason: str
    hop_path: tuple[HopPathStep, ...]


@dataclass(frozen=True)
class PrunedTwoHopConfig:
    """Hard bounds and scoring weights for a graph-RAG request.

    ``max_first_hop_candidates`` is a global cap across all seeds.  Subsequent
    expansion is bounded by it times ``max_degree``; it is therefore safe even
    when the underlying graph has high-degree hubs.
    """

    top_k: int = 8
    max_seed_candidates: int = 12
    max_first_hop_candidates: int = 16
    max_degree: int = 12
    max_hops: int = 2
    relevance_weight: float = 0.55
    confidence_weight: float = 0.25
    path_weight: float = 0.20
    hop_decay: float = 0.90

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ValueError("top_k must be positive")
        if self.max_seed_candidates < 1 or self.max_first_hop_candidates < 1:
            raise ValueError("candidate caps must be positive")
        if self.max_degree < 1:
            raise ValueError("max_degree must be positive")
        if self.max_hops not in {1, 2}:
            raise ValueError("max_hops must be 1 or 2")
        if not 0 < self.hop_decay <= 1:
            raise ValueError("hop_decay must be within (0, 1]")
        if min(self.relevance_weight, self.confidence_weight, self.path_weight) < 0:
            raise ValueError("score weights cannot be negative")
        if self.relevance_weight + self.confidence_weight + self.path_weight <= 0:
            raise ValueError("at least one score weight must be positive")


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _relation_weight(relation: str, priorities: Mapping[str, float]) -> float:
    return _unit(priorities.get(relation.casefold(), priorities.get("__default__", 0.35)))


def _score(
    relevance: float, confidence: float, path_score: float, config: PrunedTwoHopConfig,
) -> float:
    total = config.relevance_weight + config.confidence_weight + config.path_weight
    return (
        config.relevance_weight * relevance
        + config.confidence_weight * confidence
        + config.path_weight * path_score
    ) / total


def _hit_sort_key(hit: GraphRetrievalHit) -> tuple[float, float, float, int, str]:
    # Stable ordering makes the result testable and avoids arbitrary canvas churn.
    return (-hit.score, -hit.path_score, -hit.confidence, hit.hop_count, hit.node_id)


def _edge_sort_key(edge: GraphEdge, priorities: Mapping[str, float]) -> tuple[float, float, str]:
    # Relation class deliberately wins before confidence: high-confidence generic
    # "related" links must not starve explicit contradiction/support evidence.
    return (-_relation_weight(edge.relation, priorities), -_unit(edge.confidence), edge.id)


def pruned_two_hop_retrieve(
    workspace_id: str,
    seeds: Iterable[RetrievalSeed],
    neighbors: GraphNeighborProvider,
    *,
    config: PrunedTwoHopConfig | None = None,
    relation_priority: Mapping[str, float] | None = None,
) -> list[GraphRetrievalHit]:
    """Expand ranked seed nodes by at most two directed hops.

    The repository adapter is intentionally responsible for workspace access
    control.  This function forwards the workspace id to every lookup, rejects
    malformed non-outgoing edges, and excludes every path that revisits a node.
    It returns the strongest path per target node, including its exact edge path,
    so a caller can construct grounded citations without re-running traversal.
    """

    if not workspace_id.strip():
        raise ValueError("workspace_id is required")
    config = config or PrunedTwoHopConfig()
    priorities = {key.casefold(): _unit(value) for key, value in DEFAULT_RELATION_PRIORITY.items()}
    if relation_priority:
        priorities.update({key.casefold(): _unit(value) for key, value in relation_priority.items()})

    deduplicated: dict[str, RetrievalSeed] = {}
    for seed in seeds:
        if not seed.node_id:
            continue
        current = deduplicated.get(seed.node_id)
        if current is None or (_unit(seed.relevance), _unit(seed.confidence)) > (
            _unit(current.relevance), _unit(current.confidence)
        ):
            deduplicated[seed.node_id] = seed
    ranked_seeds = sorted(
        deduplicated.values(),
        key=lambda item: (-_unit(item.relevance), -_unit(item.confidence), item.node_id),
    )[: config.max_seed_candidates]

    best: dict[str, GraphRetrievalHit] = {}
    frontier: list[tuple[GraphRetrievalHit, frozenset[str]]] = []
    for seed in ranked_seeds:
        relevance, confidence = _unit(seed.relevance), _unit(seed.confidence)
        hit = GraphRetrievalHit(
            node_id=seed.node_id,
            relevance=relevance,
            confidence=confidence,
            path_score=1.0,
            score=_score(relevance, confidence, 1.0, config),
            hop_count=0,
            retrieval_reason=seed.retrieval_reason or "base_retrieval",
            hop_path=(),
        )
        best[hit.node_id] = hit
        frontier.append((hit, frozenset({hit.node_id})))

    first_hop_count = 0
    for depth in range(1, config.max_hops + 1):
        next_frontier: list[tuple[GraphRetrievalHit, frozenset[str]]] = []
        for parent, visited in sorted(frontier, key=lambda item: _hit_sort_key(item[0])):
            raw_edges = neighbors.outgoing_edges(workspace_id, parent.node_id)
            valid_edges = [
                edge for edge in raw_edges
                if edge.source_id == parent.node_id and edge.target_id and edge.target_id not in visited
            ]
            for edge in sorted(valid_edges, key=lambda item: _edge_sort_key(item, priorities))[: config.max_degree]:
                if depth == 1 and first_hop_count >= config.max_first_hop_candidates:
                    break
                relation_weight = _relation_weight(edge.relation, priorities)
                edge_confidence = _unit(edge.confidence)
                step = HopPathStep(
                    edge_id=edge.id,
                    from_node_id=edge.source_id,
                    to_node_id=edge.target_id,
                    relation=edge.relation,
                    confidence=edge_confidence,
                )
                path = parent.hop_path + (step,)
                path_score = parent.path_score * relation_weight * edge_confidence * config.hop_decay
                confidence = min(parent.confidence, edge_confidence)
                reason = (
                    f"{parent.retrieval_reason}; graph_{depth}hop:"
                    + " > ".join(path_step.relation for path_step in path)
                )
                hit = GraphRetrievalHit(
                    node_id=edge.target_id,
                    relevance=parent.relevance,
                    confidence=confidence,
                    path_score=path_score,
                    score=_score(parent.relevance, confidence, path_score, config),
                    hop_count=depth,
                    retrieval_reason=reason,
                    hop_path=path,
                )
                previous = best.get(hit.node_id)
                if previous is None or _hit_sort_key(hit) < _hit_sort_key(previous):
                    best[hit.node_id] = hit
                # Preserve the path even when it loses globally: it can still be
                # the only acyclic way to reach a useful node on the next hop.
                next_frontier.append((hit, visited | {hit.node_id}))
                if depth == 1:
                    first_hop_count += 1
        frontier = next_frontier
        if not frontier:
            break

    return sorted(best.values(), key=_hit_sort_key)[: config.top_k]
