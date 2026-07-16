"""Offline, versioned CI-014 retrieval evaluation helpers.

The golden fixture deliberately excludes model calls.  Metrics whose judgement
requires an LLM or expert are registered in the fixture but reported only by a
separate explicit evaluation run.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from time import perf_counter_ns
from typing import Any

from .models import Paper
from .rag import citations_from, chunk_pages, search
from .graph_rag import GraphEdge, PrunedTwoHopConfig, RetrievalSeed, pruned_two_hop_retrieve


@dataclass(frozen=True)
class RetrievalEvaluation:
    recall_at_k: float
    citation_precision: float
    cases: int
    quote_exact_match: float = 0.0


@dataclass(frozen=True)
class EvaluationArtifact:
    schema_version: str
    fixture_version: str
    mode: str
    metrics: dict[str, float | int | None]
    gates: dict[str, bool | None]
    cases: dict[str, int]
    query_plan: dict[str, Any]
    assessments: dict[str, dict[str, Any]]


def papers_from_golden_fixture(fixture: dict[str, Any]) -> list[Paper]:
    """Convert CI-014's portable fixture format into ordinary RAG papers."""
    papers: list[Paper] = []
    for entry in fixture["papers"]:
        paper = Paper(
            id=entry["id"], user_id="ci014", workspace_id="ci014", created_by="ci014",
            title=entry["title"],
        )
        paper.chunks = chunk_pages([(entry["page"], entry["text"])], paper.id)
        papers.append(paper)
    return papers


def evaluate_retrieval_cases(fixture: dict[str, Any], *, limit: int | None = None) -> RetrievalEvaluation:
    """Compute deterministic retrieval/citation metrics without network or LLMs."""
    papers = papers_from_golden_fixture(fixture)
    metric = fixture["metrics"]["recall_at_k"]
    k = limit or int(metric["k"])
    recalls: list[float] = []
    precisions: list[float] = []
    quote_matches: list[float] = []
    for case in fixture["retrieval_cases"]:
        expected = set(case["expected_paper_ids"])
        citations = citations_from(search(papers, case["query"], k), case["query"])
        returned = [citation.paper_id for citation in citations]
        recalls.append(len(expected.intersection(returned)) / len(expected))
        precisions.append(sum(paper_id in expected for paper_id in returned) / len(returned) if returned else 0.0)
        expected_quotes = case.get("expected_quotes", [])
        excerpts = [citation.excerpt for citation in citations]
        quote_matches.append(
            sum(any(quote == excerpt or quote in excerpt for excerpt in excerpts) for quote in expected_quotes)
            / len(expected_quotes) if expected_quotes else 1.0
        )
    return RetrievalEvaluation(
        recall_at_k=sum(recalls) / len(recalls),
        citation_precision=sum(precisions) / len(precisions),
        cases=len(recalls),
        quote_exact_match=sum(quote_matches) / len(quote_matches),
    )


class _FixtureGraph:
    def __init__(self, edges: list[GraphEdge]):
        self.edges: dict[str, list[GraphEdge]] = {}
        for edge in edges:
            self.edges.setdefault(edge.source_id, []).append(edge)

    def outgoing_edges(self, workspace_id: str, node_id: str) -> list[GraphEdge]:
        return self.edges.get(node_id, [])


def evaluate_contradiction_recall(fixture: dict[str, Any]) -> float:
    """Measure bounded graph contradiction recall without a model or network."""
    cases = fixture.get("graph_cases", [])
    if not cases:
        return 1.0
    recalled = 0
    for case in cases:
        edge = GraphEdge(
            f"edge:{case['id']}", case["seed_id"], case["target_id"],
            case["relation"], 1.0,
        )
        hits = pruned_two_hop_retrieve(
            "ci014", [RetrievalSeed(case["seed_id"], relevance=1.0)],
            _FixtureGraph([edge]), config=PrunedTwoHopConfig(top_k=4, max_hops=1),
        )
        recalled += any(
            hit.node_id == case["target_id"]
            and any(step.relation == "contradicts" for step in hit.hop_path)
            for hit in hits
        )
    return recalled / len(cases)


def evaluate_semantic_only_recall(fixture: dict[str, Any]) -> tuple[float, float]:
    """Compare full mock-vector recall with the current lexical-gated pool."""
    full, bounded = [], []
    for case in fixture.get("semantic_only_cases", []):
        expected, k = case["expected_id"], int(case.get("k", 3))
        scores = case["mock_vector_scores"]
        full_rank = sorted(case["lexical_ranking"], key=lambda item: (-scores.get(item, 0.0), item))[:k]
        pool = case["lexical_ranking"][: int(case["candidate_pool_size"])]
        bounded_rank = sorted(pool, key=lambda item: (-scores.get(item, 0.0), item))[:k]
        full.append(float(expected in full_rank))
        bounded.append(float(expected in bounded_rank))
    if not full:
        return 1.0, 1.0
    return sum(full) / len(full), sum(bounded) / len(bounded)


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return float(ordered[index])


def classify_query_plan(details: list[str]) -> list[str]:
    """Reduce plans to non-sensitive operators; raw plan text is never persisted."""
    operators: list[str] = []
    for detail in details:
        normalized = detail.upper()
        target_query = bool(re.search(
            r"\b(CHUNKS|CHUNK_EMBEDDINGS)\b|\b(SEARCH|SCAN)\s+(C|CE)\b",
            normalized,
        ))
        if "TEMP B-TREE" in normalized or "FILESORT" in normalized:
            operators.append("temp_sort")
        elif ("USING INDEX" in normalized or "USING COVERING INDEX" in normalized
              or "INDEX SCAN" in normalized or "INDEX ONLY SCAN" in normalized) and target_query:
            operators.append("index_search")
        elif "SCAN" in normalized or "SEQ SCAN" in normalized:
            operators.append("full_scan")
        else:
            operators.append("unrecognized")
    return operators


def query_plan_gate(details: list[str]) -> bool:
    """Fail closed unless the observed target query positively uses an index."""
    operators = classify_query_plan(details)
    return bool(operators) and "index_search" in operators and not {
        "full_scan", "temp_sort", "unrecognized",
    }.intersection(operators)


def build_offline_artifact(
    fixture: dict[str, Any], *, latency_samples_ms: list[float] | None = None,
    query_plan_details: list[str] | None = None,
) -> EvaluationArtifact:
    retrieval = evaluate_retrieval_cases(fixture)
    contradiction = evaluate_contradiction_recall(fixture)
    semantic_full, semantic_bounded = evaluate_semantic_only_recall(fixture)
    profile = fixture.get("performance_profile", {})
    latency = list(latency_samples_ms or [])
    plan_source = "fixture_diagnostic" if query_plan_details is None else "observed"
    plan = list(
        profile.get("sqlite_query_plan", [])
        if query_plan_details is None else query_plan_details
    )
    targets = fixture["metrics"]
    metrics: dict[str, float | int | None] = {
        "lexical_unit_recall_at_k": retrieval.recall_at_k,
        "lexical_unit_citation_precision": retrieval.citation_precision,
        "lexical_unit_quote_exact_match": retrieval.quote_exact_match,
        "graph_traversal_unit_recall": contradiction,
        "production_recall_at_k": None,
        "production_citation_precision": None,
        "production_contradiction_recall": None,
        "claim_entailment": None,
        "falsifier_coverage": None,
        "hypothesis_diversity": None,
        "expert_acceptance_rate": None,
        "semantic_full_recall_at_k": semantic_full,
        "semantic_bounded_recall_at_k": semantic_bounded,
        "p50_ms": None,
        "p95_ms": None,
        "component_p50_ms": percentile(latency, 0.50) if latency else None,
        "component_p95_ms": percentile(latency, 0.95) if latency else None,
        "cost_usd": 0.0,
    }
    gates = {
        "lexical_unit": (
            retrieval.recall_at_k >= targets["recall_at_k"]["target"]
            and retrieval.citation_precision >= targets["citation_precision"]["target"]
            and retrieval.quote_exact_match >= targets["quote_exact_match"]["target"]
        ),
        "production_retrieval": None,
        "production_contradiction": None,
        "claim_entailment": None,
        "falsifier_coverage": None,
        "hypothesis_diversity": None,
        "expert_acceptance_rate": None,
        "system_latency": None,
        "semantic_only": semantic_bounded >= targets["semantic_only_recall_at_k"]["target"],
        "indexed_query_plan": query_plan_gate(plan) if plan_source == "observed" else None,
    }
    operators = classify_query_plan(plan)
    return EvaluationArtifact(
        schema_version="ci014-evaluation-artifact-v1",
        fixture_version=fixture["schema_version"], mode="offline_mocked",
        metrics=metrics, gates=gates,
        cases={
            "retrieval": retrieval.cases,
            "graph": len(fixture.get("graph_cases", [])),
            "semantic_only": len(fixture.get("semantic_only_cases", [])),
        },
        query_plan={
            "operators": operators, "detail_count": len(plan),
            "query_text_recorded": False,
        },
        assessments={
            "latency": {
                "status": "diagnostic_only" if latency else "not_evaluated",
                "source": "in_memory_component" if latency else None,
                "declared_system_sizes": profile.get("paper_counts", []),
            },
            "cost": {"status": "offline_zero", "source": "no_model_calls"},
            "contradiction": {"status": "traversal_unit_only"},
            "production_retrieval": {"status": "not_evaluated"},
            "query_plan": {
                "status": "observed" if plan_source == "observed" else "diagnostic_only",
                "source": plan_source,
            },
        },
    )


def write_evaluation_artifact(artifact: EvaluationArtifact, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema_version": artifact.schema_version,
        "fixture_version": artifact.fixture_version,
        "mode": artifact.mode,
        "metrics": artifact.metrics,
        "gates": artifact.gates,
        "cases": artifact.cases,
        "query_plan": artifact.query_plan,
        "assessments": artifact.assessments,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def benchmark_retrieval_latency(fixture: dict[str, Any], iterations: int = 5) -> list[float]:
    """Explicit local benchmark; still contains no model/network calls."""
    samples = []
    for _ in range(max(1, iterations)):
        started = perf_counter_ns()
        evaluate_retrieval_cases(fixture)
        samples.append((perf_counter_ns() - started) / 1_000_000)
    return samples


def require_live_benchmark_opt_in() -> None:
    if os.getenv("CI014_LIVE_BENCHMARK") != "1" or not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("live benchmark requires CI014_LIVE_BENCHMARK=1 and OPENAI_API_KEY")


def run_live_model_benchmark(model: str = "gpt-5.4-nano") -> dict[str, Any]:
    """One explicit provider probe, never invoked by the offline harness/tests."""
    require_live_benchmark_opt_in()
    from openai import OpenAI

    started = perf_counter_ns()
    response = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0).responses.create(
        model=model, store=False,
        input="Return exactly: CI014 live benchmark ready",
    )
    usage = getattr(response, "usage", None)
    return {
        "schema_version": "ci014-live-model-artifact-v1",
        "mode": "live_model",
        "model": model,
        "latency_ms": (perf_counter_ns() - started) / 1_000_000,
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cost_usd": None,
        "output_verified": response.output_text.strip() == "CI014 live benchmark ready",
    }
