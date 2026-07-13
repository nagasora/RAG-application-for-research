from collections import defaultdict

import pytest

from app.graph_rag import (
    GraphEdge,
    PrunedTwoHopConfig,
    RetrievalSeed,
    pruned_two_hop_retrieve,
)


class InMemoryGraph:
    def __init__(self, edges: list[GraphEdge]):
        self.edges = defaultdict(list)
        self.calls: list[tuple[str, str]] = []
        for edge in edges:
            self.edges[edge.source_id].append(edge)

    def outgoing_edges(self, workspace_id: str, node_id: str):
        self.calls.append((workspace_id, node_id))
        return self.edges[node_id]


def _edge(edge_id: str, source: str, target: str, relation: str, confidence: float = 0.9) -> GraphEdge:
    return GraphEdge(edge_id, source, target, relation, confidence)


def test_returns_two_hop_path_and_provenance_reason():
    graph = InMemoryGraph([
        _edge("e1", "paper-result", "hypothesis", "informs", 0.9),
        _edge("e2", "hypothesis", "experiment", "formulates", 0.8),
    ])

    hits = pruned_two_hop_retrieve(
        "workspace-a", [RetrievalSeed("paper-result", relevance=0.9, retrieval_reason="hybrid")], graph,
        config=PrunedTwoHopConfig(top_k=8, max_degree=2, max_first_hop_candidates=2),
    )

    experiment = next(hit for hit in hits if hit.node_id == "experiment")
    assert experiment.hop_count == 2
    assert [step.edge_id for step in experiment.hop_path] == ["e1", "e2"]
    assert [step.relation for step in experiment.hop_path] == ["informs", "formulates"]
    assert "graph_2hop:informs > formulates" in experiment.retrieval_reason
    assert set(workspace for workspace, _ in graph.calls) == {"workspace-a"}


def test_relation_priority_degree_and_first_hop_caps_prune_hubs():
    graph = InMemoryGraph([
        _edge("generic-high-confidence", "a", "generic", "related", 1.0),
        _edge("evidence-priority", "a", "evidence", "informs", 0.4),
        _edge("second-seed", "b", "other", "informs", 0.9),
    ])

    hits = pruned_two_hop_retrieve(
        "workspace-a", [RetrievalSeed("a", 0.8), RetrievalSeed("b", 0.7)], graph,
        config=PrunedTwoHopConfig(
            top_k=8, max_degree=1, max_first_hop_candidates=1, max_hops=1,
        ),
    )

    expanded = [hit.node_id for hit in hits if hit.hop_count == 1]
    assert expanded == ["evidence"]
    assert "generic" not in expanded and "other" not in expanded


def test_cycle_is_excluded_from_paths_but_another_two_hop_target_is_kept():
    graph = InMemoryGraph([
        _edge("a-b", "a", "b", "informs"),
        _edge("b-a", "b", "a", "extends"),
        _edge("b-c", "b", "c", "supports"),
    ])

    hits = pruned_two_hop_retrieve("workspace-a", [RetrievalSeed("a", 0.9)], graph)

    assert [hit.node_id for hit in hits].count("a") == 1
    target = next(hit for hit in hits if hit.node_id == "c")
    assert [step.to_node_id for step in target.hop_path] == ["b", "c"]
    assert len({"a", *[step.to_node_id for step in target.hop_path]}) == 3


def test_top_k_selects_best_path_per_node_and_limits_output():
    graph = InMemoryGraph([
        _edge("weak", "seed", "shared", "related", 0.3),
        _edge("strong-one", "seed", "middle", "informs", 0.95),
        _edge("strong-two", "middle", "shared", "supports", 0.95),
    ])

    hits = pruned_two_hop_retrieve(
        "workspace-a", [RetrievalSeed("seed", 0.8)], graph,
        config=PrunedTwoHopConfig(top_k=1, max_degree=3, max_first_hop_candidates=3),
    )

    assert len(hits) == 1
    assert hits[0].node_id == "seed"
    # The internal best path is visible when output capacity permits it.
    all_hits = pruned_two_hop_retrieve(
        "workspace-a", [RetrievalSeed("seed", 0.8)], graph,
        config=PrunedTwoHopConfig(top_k=8, max_degree=3, max_first_hop_candidates=3),
    )
    shared = next(hit for hit in all_hits if hit.node_id == "shared")
    assert [step.edge_id for step in shared.hop_path] == ["strong-one", "strong-two"]


def test_rejects_invalid_bounds_and_requires_workspace_id():
    graph = InMemoryGraph([])
    with pytest.raises(ValueError, match="workspace_id"):
        pruned_two_hop_retrieve(" ", [], graph)
    with pytest.raises(ValueError, match="max_hops"):
        PrunedTwoHopConfig(max_hops=3)
