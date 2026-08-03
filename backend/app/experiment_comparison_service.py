"""Experiment comparison orchestration, independent from FastAPI.

The service validates source-grounded profiles, owns the fallback/cache policy,
and delegates all database writes to ``PaperStore``.  HTTP routes only perform
authorization, selection resolution, and error translation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Any, Protocol

from .experiment_analysis import (
    ExperimentAnalysisError, analyze_full_text_paper, apply_verified_proposals,
    build_comparison_matrix, cache_key_for, profile_from_snapshot, profile_snapshot,
)
from .experiment_proposal import PROMPT_VERSION, generate_experiment_proposals
from .models import (
    ExperimentAnalysisErrorResponse, ExperimentComparisonRequest,
    ExperimentComparisonResponse, ExperimentMatrixRowResponse,
    ExperimentProfileResponse,
)

_PROFILE_EVIDENCE_FIELDS = (
    "purpose", "design", "datasets", "sample_sizes", "interventions",
    "comparators", "conditions", "metrics", "observations",
    "author_interpretations", "limitations",
)


class ExperimentComparisonStore(Protocol):
    def get_owned(self, workspace_id: str, paper_id: str) -> Any: ...
    def paper_source_version_id(self, workspace_id: str, paper_id: str) -> str: ...
    def paper_pages(self, workspace_id: str, paper_id: str) -> Any: ...
    def list_document_elements(self, workspace_id: str, paper_id: str) -> Any: ...
    def get_experiment_profile_snapshot(self, workspace_id: str, paper_id: str, **identity) -> dict | None: ...
    def record_generation_audit(self, workspace_id: str, user_id: str, **audit) -> None: ...
    def create_source_import(self, workspace_id: str, **source) -> tuple[Any, list[Any]]: ...
    def save_experiment_profile_snapshot(self, workspace_id: str, profile, **snapshot) -> dict: ...


def _source_span_binding_key(value) -> str:
    """Return the immutable evidence identity used across idempotent retries."""

    if isinstance(value, dict):
        get = value.get
    else:
        get = lambda name, default=None: getattr(value, name, default)
    return json.dumps(
        {
            "page": get("page"),
            "bbox": get("bbox"),
            "cell": get("cell"),
            "locator": get("locator", {}) or {},
            "text": get("text", "") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _attach_source_span_ids(bindings: list[tuple[dict, dict]], spans: list) -> list[str]:
    """Bind returned spans by content, never by database return order."""

    spans_by_key: dict[str, list] = {}
    for span in spans:
        spans_by_key.setdefault(_source_span_binding_key(span), []).append(span)
    assigned: list[str] = []
    for evidence, span_input in bindings:
        candidates = spans_by_key.get(_source_span_binding_key(span_input), [])
        if not candidates:
            raise RuntimeError("source span mapping is incomplete")
        span = candidates.pop(0)
        evidence.setdefault("locator", {})["source_span_id"] = span.id
        assigned.append(span.id)
    if any(candidates for candidates in spans_by_key.values()):
        raise RuntimeError("source span mapping contains unused spans")
    return assigned


def _response_from_snapshot(*, paper, source_version_id: str, snapshot: dict, status: str, cached: bool) -> ExperimentProfileResponse:
    return ExperimentProfileResponse(
        profile_id=str(snapshot.get("profile_id") or "") or None,
        paper_id=paper.id, title=paper.title, status=status, cached=cached,
        progress="cached" if cached else "completed",
        review_status=str(snapshot.get("review_status") or "review_pending"),
        source_version_id=source_version_id,
        derived_source_version_id=snapshot.get("derived_source_version_id"),
        source_span_ids=list(snapshot.get("source_span_ids") or []),
        generation_provider=str(snapshot.get("generation_provider") or "local"),
        generation_model=str(snapshot.get("generation_model") or "evidence-extractor-v1"),
        generation_prompt_version=str(snapshot.get("generation_prompt_version") or "experiment-analysis-v1"),
        generation_usage=dict(snapshot.get("generation_usage") or {}),
        extraction_mode=str(snapshot.get("extraction_mode") or "deterministic_local"),
        proposal_candidates_accepted=int(snapshot.get("proposal_candidates_accepted") or 0),
        proposal_candidates_rejected=int(snapshot.get("proposal_candidates_rejected") or 0),
        purpose=snapshot.get("purpose", []), design=snapshot.get("design", []),
        observations=snapshot.get("observations", []),
        author_interpretations=snapshot.get("author_interpretations", []),
        limitations=snapshot.get("limitations", []),
        figure_table_refs=snapshot.get("figure_table_refs", []),
    )


def compare_experiment_profiles(body: ExperimentComparisonRequest, store: ExperimentComparisonStore, *, workspace_id: str, user_id: str, selection, proposal_generator=generate_experiment_proposals) -> ExperimentComparisonResponse:
    profiles = []
    responses: list[ExperimentProfileResponse] = []
    errors: list[ExperimentAnalysisErrorResponse] = []
    for paper_id in body.paper_ids:
        try:
            paper = store.get_owned(workspace_id, paper_id)
            if paper.content_scope != "full_text":
                raise ExperimentAnalysisError("full_text_required", "full text is required")
            source_version_id = store.paper_source_version_id(workspace_id, paper.id)
            selected_key = cache_key_for(paper.content_hash, selection.model, PROMPT_VERSION)
            cached = store.get_experiment_profile_snapshot(
                workspace_id, paper.id, source_version_id=source_version_id,
                content_hash=paper.content_hash, provider=selection.provider,
                model=selection.model, prompt_version=PROMPT_VERSION, cache_key=selected_key,
            )
            if cached is not None:
                profiles.append(profile_from_snapshot(cached))
                responses.append(_response_from_snapshot(paper=paper, source_version_id=source_version_id, snapshot=cached, status="cached", cached=True))
                continue

            pages = store.paper_pages(workspace_id, paper.id)
            elements = store.list_document_elements(workspace_id, paper.id)
            proposal = proposal_generator(selection, pages)
            effective_provider = selection.provider if proposal.succeeded else "local"
            effective_model = selection.model if proposal.succeeded else "evidence-extractor-v1"
            effective_prompt = PROMPT_VERSION if proposal.succeeded else "experiment-analysis-v1"
            effective_key = cache_key_for(paper.content_hash, effective_model, effective_prompt)
            if not proposal.succeeded:
                cached = store.get_experiment_profile_snapshot(
                    workspace_id, paper.id, source_version_id=source_version_id,
                    content_hash=paper.content_hash, provider="local", model=effective_model,
                    prompt_version=effective_prompt, cache_key=effective_key,
                )
                if cached is not None:
                    store.record_generation_audit(
                        workspace_id, user_id, scope="analysis",
                        provider=selection.provider, model=selection.model,
                        outcome="failed", reference_id=paper.id,
                        prompt_version=PROMPT_VERSION, usage=proposal.usage,
                    )
                    store.record_generation_audit(
                        workspace_id, user_id, scope="analysis",
                        provider="local", model="evidence-extractor-v1",
                        outcome="cache_hit", reference_id=paper.id,
                        prompt_version="experiment-analysis-v1",
                    )
                    profiles.append(profile_from_snapshot(cached))
                    responses.append(_response_from_snapshot(paper=paper, source_version_id=source_version_id, snapshot=cached, status="cached", cached=True))
                    continue

            profile = analyze_full_text_paper(
                paper, pages, elements, source_version_id=source_version_id,
                model="evidence-extractor-v1", prompt_version="experiment-analysis-v1",
            )
            accepted = rejected = 0
            if proposal.succeeded:
                profile, accepted, rejected = apply_verified_proposals(profile, proposal.proposals, pages, elements)
                store.record_generation_audit(workspace_id, user_id, scope="analysis", provider=selection.provider, model=selection.model, outcome="succeeded" if accepted else "unverified_proposals_rejected", reference_id=paper.id, prompt_version=PROMPT_VERSION, usage=proposal.usage)
            else:
                store.record_generation_audit(workspace_id, user_id, scope="analysis", provider=selection.provider, model=selection.model, outcome="failed", reference_id=paper.id, prompt_version=PROMPT_VERSION, usage=proposal.usage)
            if accepted:
                effective_provider = selection.provider
                effective_model = selection.model
                effective_prompt = PROMPT_VERSION
                effective_key = selected_key
                profile = replace(
                    profile, model=effective_model, prompt_version=effective_prompt,
                    cache_key=effective_key,
                )
            else:
                effective_provider = "local"
                effective_model = "evidence-extractor-v1"
                effective_prompt = "experiment-analysis-v1"
                effective_key = cache_key_for(paper.content_hash, effective_model, effective_prompt)
                profile = replace(
                    profile, model=effective_model, prompt_version=effective_prompt,
                    cache_key=effective_key,
                )
                store.record_generation_audit(
                    workspace_id, user_id, scope="analysis",
                    provider="local", model=effective_model, outcome="succeeded",
                    reference_id=paper.id, prompt_version=effective_prompt,
                )
            snapshot = profile_snapshot(profile)
            snapshot.update({"generation_provider": effective_provider, "generation_model": effective_model, "generation_prompt_version": effective_prompt, "generation_usage": proposal.usage, "extraction_mode": "llm_verified" if accepted else "deterministic_local", "proposal_candidates_accepted": accepted, "proposal_candidates_rejected": rejected})
            span_inputs: list[dict] = []
            span_bindings: list[tuple[dict, dict]] = []
            for field in _PROFILE_EVIDENCE_FIELDS:
                for evidence_index, evidence in enumerate(snapshot.get(field, [])):
                    locator = evidence.get("locator", {})
                    span_input = {
                        "page": locator.get("page"),
                        "bbox": locator.get("bbox"),
                        "cell": locator.get("cell"),
                        "locator": {
                            "paper_id": paper.id,
                            "element_id": locator.get("element_id"),
                            "source_kind": locator.get("source_kind"),
                            "profile_field": field,
                            "evidence_index": evidence_index,
                        },
                        "text": locator.get("quote", ""),
                    }
                    span_inputs.append(span_input)
                    span_bindings.append((evidence, span_input))
            serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            derived, spans = store.create_source_import(workspace_id, kind="experiment_profile", locator=f"paper:{paper.id}:experiment:{profile.cache_key}", content_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(), metadata={"original_source_version_id": source_version_id, "original_content_hash": paper.content_hash, "provider": effective_provider, "model": profile.model, "prompt_version": profile.prompt_version, "usage": proposal.usage, "extraction_mode": snapshot["extraction_mode"]}, spans=span_inputs, paper_id=paper.id)
            assigned_span_ids = _attach_source_span_ids(span_bindings, spans)
            snapshot["derived_source_version_id"] = derived.id
            snapshot["source_span_ids"] = assigned_span_ids
            snapshot = store.save_experiment_profile_snapshot(
                workspace_id, profile, provider=effective_provider, snapshot_override=snapshot,
            )
            profiles.append(profile_from_snapshot(snapshot))
            responses.append(_response_from_snapshot(paper=paper, source_version_id=source_version_id, snapshot=snapshot, status="ready", cached=False))
        except ExperimentAnalysisError as exc:
            errors.append(ExperimentAnalysisErrorResponse(paper_id=paper_id, code=exc.code, message=str(exc)))
        except Exception as exc:
            # Store boundaries intentionally expose no exception details.
            from .store import PaperNotFoundError
            if isinstance(exc, PaperNotFoundError):
                errors.append(ExperimentAnalysisErrorResponse(paper_id=paper_id, code="paper_not_found", message="paper not found"))
            else:
                raise
    return ExperimentComparisonResponse(
        profiles=responses, matrix=[ExperimentMatrixRowResponse(**asdict(row)) for row in build_comparison_matrix(profiles)],
        errors=errors, generation_provider=selection.provider, generation_model=selection.model,
    )
