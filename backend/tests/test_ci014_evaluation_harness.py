import hashlib
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.evaluation import (
    build_offline_artifact, evaluate_retrieval_cases, query_plan_gate,
    require_live_benchmark_opt_in, write_evaluation_artifact,
)
from app.graph_rag import GraphEdge, PrunedTwoHopConfig, RetrievalSeed, pruned_two_hop_retrieve
from app.models import Principal
from app.store import PaperStore


FIXTURE_PATH = Path(__file__).parents[1] / "evaluation" / "ci014_golden_cases.json"


class _Graph:
    def __init__(self, edges):
        self.edges = defaultdict(list)
        for edge in edges:
            self.edges[edge.source_id].append(edge)

    def outgoing_edges(self, workspace_id, node_id):
        assert workspace_id == "ci014"
        return self.edges[node_id]


def _fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_ci014_fixture_is_versioned_and_covers_required_failure_modes():
    fixture = _fixture()
    assert fixture["schema_version"] == "ci014-v2"
    assert {"japanese-causal-numeric", "english-negation", "numeric-result", "low-quality-ocr"} <= {
        case["id"] for case in fixture["retrieval_cases"]
    }
    assert any(paper.get("extraction_quality") == "low" for paper in fixture["papers"])
    assert fixture["graph_cases"] and fixture["reingestion_cases"]
    assert {"recall_at_k", "citation_precision", "claim_entailment", "contradiction_recall", "falsifier_coverage", "hypothesis_diversity", "expert_acceptance_rate", "p95_ms", "cost_usd", "quote_exact_match", "semantic_only_recall_at_k", "indexed_query_plan"} <= set(fixture["metrics"])
    assert fixture["semantic_only_cases"] and fixture["performance_profile"]["paper_counts"] == [100, 500, 5000]


def test_ci014_offline_retrieval_and_citation_metrics_meet_golden_targets():
    fixture = _fixture()
    result = evaluate_retrieval_cases(fixture)
    assert result.cases == len(fixture["retrieval_cases"])
    assert result.recall_at_k >= fixture["metrics"]["recall_at_k"]["target"]
    assert result.citation_precision >= fixture["metrics"]["citation_precision"]["target"]
    assert result.quote_exact_match >= fixture["metrics"]["quote_exact_match"]["target"]


def test_ci014_offline_artifact_exposes_ci019_unresolved_gates_without_network(tmp_path):
    artifact = build_offline_artifact(_fixture())
    assert artifact.mode == "offline_mocked"
    assert artifact.metrics["graph_traversal_unit_recall"] == 1.0
    assert artifact.metrics["production_recall_at_k"] is None
    assert artifact.metrics["production_contradiction_recall"] is None
    assert artifact.metrics["semantic_full_recall_at_k"] == 1.0
    assert artifact.metrics["semantic_bounded_recall_at_k"] == 0.0
    assert artifact.gates["semantic_only"] is False
    assert artifact.gates["indexed_query_plan"] is None
    assert artifact.gates["production_retrieval"] is None
    assert artifact.gates["production_contradiction"] is None
    assert artifact.gates["claim_entailment"] is None
    assert artifact.gates["falsifier_coverage"] is None
    assert artifact.gates["hypothesis_diversity"] is None
    assert artifact.gates["expert_acceptance_rate"] is None
    assert artifact.metrics["p95_ms"] is None
    assert artifact.metrics["component_p95_ms"] is None
    assert artifact.metrics["cost_usd"] == 0.0
    assert artifact.query_plan["query_text_recorded"] is False
    assert "details" not in artifact.query_plan
    assert artifact.assessments["latency"]["status"] == "not_evaluated"
    output = tmp_path / "artifact.json"
    write_evaluation_artifact(artifact, output)
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "ci014-evaluation-artifact-v1"
    assert persisted["fixture_version"] == "ci014-v2"


def test_ci014_query_plan_gate_fails_closed_and_never_persists_raw_details():
    assert query_plan_gate([]) is False
    assert query_plan_gate(["SEARCH c USING INDEX ix_chunks_paper_id (paper_id=?)"]) is True
    assert query_plan_gate(["Index Scan using ix_chunks_paper_id on chunks"]) is True
    assert query_plan_gate(["SEARCH users USING INDEX ix_users_email"]) is False
    assert query_plan_gate(["SCAN chunks", "USE TEMP B-TREE FOR ORDER BY"]) is False
    secret = "private-query-token"
    artifact = build_offline_artifact(
        _fixture(), query_plan_details=[
            f"SEARCH c USING INDEX ix_chunks_paper_id query={secret}",
        ],
    )
    serialized = json.dumps(artifact.query_plan)
    assert secret not in serialized
    assert artifact.query_plan["operators"] == ["index_search"]

    positive_fixture = _fixture()
    positive_fixture["performance_profile"]["sqlite_query_plan"] = [
        "SEARCH c USING INDEX ix_chunks_paper_id (paper_id=?)",
    ]
    explicitly_empty = build_offline_artifact(
        positive_fixture, query_plan_details=[],
    )
    assert explicitly_empty.gates["indexed_query_plan"] is False
    assert explicitly_empty.assessments["query_plan"] == {
        "status": "observed", "source": "observed",
    }


def test_ci014_live_benchmark_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("CI014_LIVE_BENCHMARK", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        require_live_benchmark_opt_in()
    except RuntimeError as exc:
        assert "CI014_LIVE_BENCHMARK=1" in str(exc)
    else:
        raise AssertionError("live benchmark must not run implicitly")


def test_ci014_graph_contradiction_is_recalled_with_auditable_path():
    case = _fixture()["graph_cases"][0]
    graph = _Graph([GraphEdge("contradiction-edge", case["seed_id"], case["target_id"], case["relation"], 0.95)])
    hits = pruned_two_hop_retrieve(
        "ci014", [RetrievalSeed(case["seed_id"], relevance=0.9)], graph,
        config=PrunedTwoHopConfig(top_k=3, max_hops=1),
    )
    hit = next(hit for hit in hits if hit.node_id == case["target_id"])
    assert hit.hop_path[0].relation == "contradicts"


def test_ci014_reingestion_keeps_prior_revision_and_is_idempotent(tmp_path):
    case = _fixture()["reingestion_cases"][0]
    engine = create_engine(f"sqlite:///{tmp_path / 'ci014.db'}")
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    _, workspace = store.ensure_user(Principal(issuer="ci014", subject="evaluator"))

    def import_revision(content):
        return store.create_source_import(
            workspace.id, kind=case["kind"], locator=case["locator"],
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(), metadata={},
            spans=[{"page": 1, "char_start": 0, "char_end": len(content), "text": content}],
        )

    original, original_spans = import_revision(case["content"])
    repeated, repeated_spans = import_revision(case["content"])
    corrected, corrected_spans = import_revision(case["replacement_content"])
    assert repeated.id == original.id and repeated_spans[0].id == original_spans[0].id
    assert corrected.id != original.id
    assert store.get_source_span(workspace.id, original_spans[0].id).text == case["content"]
    assert corrected_spans[0].text == case["replacement_content"]
