"""Deterministic, evidence-first helpers for comparing full-text experiments.

This module deliberately has no database or model-provider dependency.  The
API layer is responsible for choosing a generation model, while these helpers
make sure every displayed observation can still be traced to an immutable
paper page, table cell, or caption.  It is therefore safe to use in the
offline/no-LLM path as well as as a verifier for an LLM proposal.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, replace
from typing import Iterable, Mapping, Sequence

from .models import DocumentElement, Paper, PaperPage


class ExperimentAnalysisError(ValueError):
    """A stable error suitable for an API error mapper."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_CAPTION_RE = re.compile(
    r"^\s*(?P<label>(?P<kind>figure|fig\.?|table|図|表)\s*(?P<number>[0-9０-９]+[A-Za-zＡ-Ｚ]?))\s*[:.：]?\s*(?P<body>.+)$",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[。！？])\s*|(?<=[.!?])\s+|\n+")
_UNIT_PATTERN = r"%|mg|kg|g|µg|μg|ug|ms|sec|s|min|h|hours?|days?|mL|L|mm|cm|m|Hz|kHz|MHz|°C"
_NUMBER_RE = re.compile(
    rf"(?<![\w.])(?:p\s*[<=>]\s*\.?\d+(?:\.\d+)?|n\s*=\s*\d+|[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*(?:{_UNIT_PATTERN}))?)(?!\w)",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(rf"({_UNIT_PATTERN})\s*$", re.IGNORECASE)
_SECTION_HEADING_RE = re.compile(
    r"(?im)(?:^|[\n\r]|(?<=[.!?。！？])\s*)(results?|実験結果|結果|discussion|考察|conclusions?|結論)(?:\s*[:：.。]|(?=\s*(?:\r?\n|$)))"
)
_OBSERVATION_WORDS = ("result", "results", "improv", "outperform", "increase", "decrease", "accuracy", "効果", "結果", "改善", "低下", "増加", "減少")
_INTERPRETATION_WORDS = ("suggest", "indicate", "imply", "demonstrat", "we believe", "考察", "示唆", "示して", "解釈", "と考え")
_LIMITATION_WORDS = ("limitation", "future work", "limited", "課題", "限界", "今後")
_PROMPT_INJECTION_WORDS = ("ignore previous", "system prompt", "developer message", "以前の指示を無視", "システムプロンプト")


@dataclass(frozen=True)
class EvidenceLocator:
    """An immutable, page-addressable anchor for one displayed fact."""

    page: int
    quote: str
    source_kind: str = "page_text"
    source_span_id: str | None = None
    element_id: str | None = None
    bbox: tuple[float, ...] | None = None
    cell: dict[str, int] | None = None


@dataclass(frozen=True)
class Measurement:
    """A literal numeric token from an evidence quote; never a graph estimate."""

    raw: str
    value: float | None
    unit: str | None = None
    kind: str = "reported_value"


@dataclass(frozen=True)
class ExperimentEvidence:
    kind: str
    text: str
    locator: EvidenceLocator
    measurements: tuple[Measurement, ...] = ()
    comparator: str | None = None
    quality: str = "high"


@dataclass(frozen=True)
class FigureTableAssociation:
    page: int
    target_element_id: str | None
    caption_element_id: str | None
    target_kind: str
    label: str | None
    caption: str
    relation: str
    confidence: str


@dataclass(frozen=True)
class ExperimentProfile:
    """A review-pending snapshot that only contains source-faithful findings."""

    paper_id: str
    source_version_id: str
    content_hash: str
    model: str
    prompt_version: str
    cache_key: str
    review_status: str = "review_pending"
    purpose: tuple[ExperimentEvidence, ...] = ()
    design: tuple[ExperimentEvidence, ...] = ()
    datasets: tuple[ExperimentEvidence, ...] = ()
    sample_sizes: tuple[ExperimentEvidence, ...] = ()
    interventions: tuple[ExperimentEvidence, ...] = ()
    comparators: tuple[ExperimentEvidence, ...] = ()
    conditions: tuple[ExperimentEvidence, ...] = ()
    metrics: tuple[ExperimentEvidence, ...] = ()
    observations: tuple[ExperimentEvidence, ...] = ()
    author_interpretations: tuple[ExperimentEvidence, ...] = ()
    limitations: tuple[ExperimentEvidence, ...] = ()
    figure_table_refs: tuple[FigureTableAssociation, ...] = ()


@dataclass(frozen=True)
class ComparisonCell:
    key: str
    text: str
    evidence: tuple[ExperimentEvidence, ...]
    status: str = "grounded"


@dataclass(frozen=True)
class ComparisonRow:
    paper_id: str
    cells: tuple[ComparisonCell, ...]


def cache_key_for(content_hash: str, model: str, prompt_version: str) -> str:
    """Changing the original, model, or prompt must invalidate a cached profile."""

    material = "\x1f".join((content_hash, model, prompt_version)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _normalised_kind(raw: str) -> str:
    raw = raw.casefold().rstrip(".")
    if raw in {"figure", "fig", "図"}:
        return "figure"
    return "table"


def parse_caption(text: str) -> tuple[str, str | None, str] | None:
    """Return target kind, label, and literal caption only when it is labelled."""

    match = _CAPTION_RE.match(text.strip())
    if not match:
        return None
    caption = match.group(0).strip()
    return _normalised_kind(match.group("kind")), match.group("label"), caption


def associate_figure_table_captions(elements: Sequence[DocumentElement]) -> list[FigureTableAssociation]:
    """Associate only unambiguous same-page captions.

    A caption is linked only when an explicit ``related_element_id`` points to
    the visual.  A sole same-page visual remains a medium-confidence reference,
    never a strict caption relation: publisher marks and vector figures can be
    extracted as separate element types even when only one raster image exists.
    """

    by_page: dict[int, list[DocumentElement]] = {}
    for element in elements:
        by_page.setdefault(element.page, []).append(element)
    associations: list[FigureTableAssociation] = []
    for page, page_elements in sorted(by_page.items()):
        targets = {kind: [item for item in page_elements if item.kind == kind] for kind in ("figure", "table")}
        for caption_element in (item for item in page_elements if item.kind == "caption" and item.text):
            metadata = caption_element.structured_data if isinstance(caption_element.structured_data, dict) else {}
            parsed = parse_caption(caption_element.text)
            target_kind = str(metadata.get("target_kind") or (parsed[0] if parsed else ""))
            if target_kind not in {"figure", "table"}:
                continue
            label = str(metadata.get("label") or (parsed[1] if parsed else "")) or None
            explicit_id = metadata.get("related_element_id")
            target = next((item for item in targets[target_kind] if item.id == explicit_id), None)
            verified_relation = (
                metadata.get("caption_relation") == "caption_for"
                and metadata.get("caption_relation_confidence") == "high"
            )
            if target is not None and verified_relation:
                relation, confidence = "caption_for", "high"
            elif target is not None:
                relation, confidence = "unresolved", "medium"
            elif len(derived_targets := [
                item for item in targets[target_kind]
                if isinstance(item.structured_data, dict)
                and item.structured_data.get("caption_element_id") == caption_element.id
                and item.structured_data.get("crop_confidence") == "medium"
            ]) == 1:
                target, relation, confidence = derived_targets[0], "unresolved", "medium"
            elif len(targets[target_kind]) == 1:
                target, relation, confidence = targets[target_kind][0], "unresolved", "medium"
            else:
                target, relation, confidence = None, "unresolved", "unknown"
            associations.append(FigureTableAssociation(
                page=page, target_element_id=target.id if target else None,
                caption_element_id=caption_element.id, target_kind=target_kind,
                label=label, caption=caption_element.text, relation=relation, confidence=confidence,
            ))
    return associations


def _page_texts(pages: Mapping[int, str] | Sequence[PaperPage]) -> dict[int, str]:
    if isinstance(pages, Mapping):
        return {int(page): str(text) for page, text in pages.items()}
    return {page.page: page.text for page in pages}


def _contains_injection(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _PROMPT_INJECTION_WORDS)


def _measurement_tokens(quote: str) -> tuple[Measurement, ...]:
    values: list[Measurement] = []
    for match in _NUMBER_RE.finditer(quote):
        raw = match.group(0).strip()
        lowered = raw.casefold().replace(" ", "")
        kind = "p_value" if lowered.startswith("p") else "sample_size" if lowered.startswith("n=") else "reported_value"
        unit_match = _UNIT_RE.search(raw)
        unit = unit_match.group(1) if unit_match else None
        cleaned = re.sub(r"^(?:p[<=>]?|n=)", "", lowered)
        if unit:
            cleaned = cleaned[: -len(unit.casefold())]
        try:
            value = float(cleaned)
        except ValueError:
            value = None
        values.append(Measurement(raw=raw, value=value, unit=unit, kind=kind))
    return tuple(values)


def _section_for_quote(page_text: str, quote: str) -> str | None:
    """Return the nearest explicit Results/Discussion heading before a quote."""

    position = page_text.find(quote)
    if position < 0:
        return None
    probe = page_text[: position + min(len(quote), 32)]
    matches = list(_SECTION_HEADING_RE.finditer(probe))
    if not matches:
        return None
    heading = matches[-1].group(1).casefold()
    return "discussion" if heading.startswith(("discussion", "考察", "conclusion", "結論")) else "results"


def _valid_comparator(quote: str, comparator: str) -> bool:
    comparator = comparator.strip()
    if not comparator or comparator not in quote:
        return False
    explicit = ("baseline", "control", "placebo", "対照", "比較対象", "ベースライン")
    if any(marker in comparator.casefold() for marker in explicit):
        return True
    escaped = re.escape(comparator)
    return bool(
        re.search(rf"\b(?:vs\.?|versus|compared\s+(?:with|to)|than)\s+(?:the\s+)?{escaped}\b", quote, re.IGNORECASE)
        or re.search(rf"(?:対照|比較対象)(?:は|として)?\s*{escaped}|{escaped}(?:と|との)?比較", quote)
    )


def evidence_from_quote(
    *, page: int, quote: str, page_text: str, kind: str, element: DocumentElement | None = None,
    cell: dict[str, int] | None = None, comparator: str | None = None,
) -> ExperimentEvidence:
    """Build evidence only if the exact displayed quote exists in its source."""

    quote = quote.strip()
    if not quote:
        raise ExperimentAnalysisError("empty_quote", "evidence quote must not be empty")
    source = element.text if element is not None and element.text else page_text
    if quote not in source:
        raise ExperimentAnalysisError("quote_not_in_source", "evidence quote does not exactly match its source")
    if _contains_injection(quote):
        raise ExperimentAnalysisError("untrusted_document_instruction", "document instructions cannot become experiment evidence")
    bbox = tuple(element.bbox) if element is not None and element.bbox else None
    locator = EvidenceLocator(
        page=page, quote=quote, source_kind=element.kind if element is not None else "page_text",
        element_id=element.id if element is not None else None, bbox=bbox, cell=cell,
    )
    return ExperimentEvidence(kind=kind, text=quote, locator=locator, measurements=_measurement_tokens(quote), comparator=comparator)


def _sentences(text: str) -> Iterable[str]:
    for sentence in _SENTENCE_RE.split(text):
        sentence = sentence.strip()
        if sentence:
            yield sentence


def _has(sentence: str, words: Sequence[str]) -> bool:
    lowered = sentence.casefold()
    return any(word in lowered for word in words)


def _evidence_for_sentences(
    page_texts: Mapping[int, str], *, kind: str, words: Sequence[str],
    numbers_required: bool = False, required_section: str | None = None,
) -> tuple[ExperimentEvidence, ...]:
    evidence: list[ExperimentEvidence] = []
    seen: set[tuple[int, str, str]] = set()
    for page, text in sorted(page_texts.items()):
        for sentence in _sentences(text):
            if sentence.strip().rstrip(".:：。").casefold() in {
                "result", "results", "実験結果", "結果",
                "discussion", "考察", "conclusion", "conclusions", "結論",
            }:
                continue
            if not _has(sentence, words) or (numbers_required and not _NUMBER_RE.search(sentence)):
                continue
            if required_section and _section_for_quote(text, sentence) != required_section:
                continue
            identity = (page, kind, sentence)
            if identity in seen:
                continue
            seen.add(identity)
            try:
                evidence.append(evidence_from_quote(page=page, quote=sentence, page_text=text, kind=kind))
            except ExperimentAnalysisError:
                continue
    return tuple(evidence)


def _table_observations(elements: Sequence[DocumentElement], page_texts: Mapping[int, str]) -> tuple[ExperimentEvidence, ...]:
    observations: list[ExperimentEvidence] = []
    for element in elements:
        if element.kind != "table" or not isinstance(element.structured_data, dict):
            continue
        rows = element.structured_data.get("rows")
        if not isinstance(rows, list):
            continue
        for row_index, row in enumerate(rows):
            if not isinstance(row, list):
                continue
            for column_index, value in enumerate(row):
                quote = str(value or "").strip()
                if not quote or not _NUMBER_RE.search(quote):
                    continue
                try:
                    observations.append(evidence_from_quote(
                        page=element.page, quote=quote, page_text=page_texts.get(element.page, ""),
                        kind="observation", element=element, cell={"row": row_index, "column": column_index},
                    ))
                except ExperimentAnalysisError:
                    continue
    return tuple(observations)


def analyze_full_text_paper(
    paper: Paper, pages: Mapping[int, str] | Sequence[PaperPage], elements: Sequence[DocumentElement], *,
    source_version_id: str, model: str, prompt_version: str,
) -> ExperimentProfile:
    """Create a conservative, review-pending experiment profile without an LLM.

    An LLM integration may propose additions, but it should pass every proposed
    quote through :func:`evidence_from_quote` before it is persisted.
    """

    if paper.content_scope != "full_text":
        raise ExperimentAnalysisError("full_text_required", "experiment analysis requires a full-text paper")
    if not source_version_id:
        raise ExperimentAnalysisError("source_version_required", "a source version is required for provenance")
    page_texts = _page_texts(pages)
    if not any(text.strip() for text in page_texts.values()):
        raise ExperimentAnalysisError("empty_document", "experiment analysis requires extracted page text")
    content_hash = paper.content_hash or hashlib.sha256(
        "\x1e".join(f"{page}:{text}" for page, text in sorted(page_texts.items())).encode("utf-8")
    ).hexdigest()
    observations = _evidence_for_sentences(
        page_texts, kind="observation", words=_OBSERVATION_WORDS,
        numbers_required=True, required_section="results",
    )
    observations += _table_observations(elements, page_texts)
    return ExperimentProfile(
        paper_id=paper.id, source_version_id=source_version_id, content_hash=content_hash,
        model=model, prompt_version=prompt_version, cache_key=cache_key_for(content_hash, model, prompt_version),
        purpose=_evidence_for_sentences(page_texts, kind="purpose", words=("aim", "objective", "目的", "本研究")),
        design=_evidence_for_sentences(page_texts, kind="design", words=("random", "trial", "experiment", "実験", "無作為")),
        datasets=_evidence_for_sentences(page_texts, kind="dataset", words=("dataset", "cohort", "data set", "データセット", "コホート")),
        sample_sizes=_evidence_for_sentences(page_texts, kind="sample_size", words=("n =", "n=", "participants", "subjects", "参加者", "被験者")),
        interventions=_evidence_for_sentences(page_texts, kind="intervention", words=("intervention", "treatment", "proposed", "提案手法", "介入")),
        comparators=_evidence_for_sentences(page_texts, kind="comparator", words=("baseline", "control", "compared", "対照", "比較")),
        conditions=_evidence_for_sentences(page_texts, kind="condition", words=("condition", "setting", "under", "条件", "設定")),
        metrics=_evidence_for_sentences(page_texts, kind="metric", words=("accuracy", "f1", "auc", "metric", "評価指標", "精度")),
        observations=observations,
        author_interpretations=_evidence_for_sentences(
            page_texts, kind="author_interpretation", words=_INTERPRETATION_WORDS,
            required_section="discussion",
        ),
        limitations=_evidence_for_sentences(page_texts, kind="limitation", words=_LIMITATION_WORDS),
        figure_table_refs=tuple(associate_figure_table_captions(elements)),
    )


def build_comparison_matrix(profiles: Sequence[ExperimentProfile]) -> list[ComparisonRow]:
    """Return only grounded cells; missing facts are explicitly unresolved."""

    fields = ("purpose", "design", "datasets", "sample_sizes", "interventions", "comparators", "conditions", "metrics", "observations", "author_interpretations", "limitations")
    rows: list[ComparisonRow] = []
    for profile in profiles:
        cells: list[ComparisonCell] = []
        for key in fields:
            evidence = getattr(profile, key)
            if evidence:
                cells.append(ComparisonCell(key=key, text="\n".join(item.text for item in evidence), evidence=evidence))
            else:
                cells.append(ComparisonCell(key=key, text="未報告", evidence=(), status="unresolved"))
        rows.append(ComparisonRow(paper_id=profile.paper_id, cells=tuple(cells)))
    return rows


_PROFILE_EVIDENCE_FIELDS = (
    "purpose", "design", "datasets", "sample_sizes", "interventions", "comparators", "conditions", "metrics",
    "observations", "author_interpretations", "limitations",
)


def profile_snapshot(profile: ExperimentProfile) -> dict:
    """Return the JSON-safe immutable representation persisted by the store."""

    return asdict(profile)


def profile_from_snapshot(snapshot: Mapping[str, object]) -> ExperimentProfile:
    """Rehydrate a stored snapshot so cache hits participate in the matrix.

    Cache records are untrusted persistence input: malformed evidence is omitted
    instead of making a comparison endpoint fail for every paper.
    """

    def evidence(value: object) -> ExperimentEvidence | None:
        if not isinstance(value, Mapping) or not isinstance(value.get("locator"), Mapping):
            return None
        locator_value = value["locator"]
        try:
            bbox = locator_value.get("bbox")
            locator = EvidenceLocator(
                page=int(locator_value["page"]), quote=str(locator_value["quote"]),
                source_kind=str(locator_value.get("source_kind") or "page_text"),
                source_span_id=str(locator_value["source_span_id"]) if locator_value.get("source_span_id") else None,
                element_id=str(locator_value["element_id"]) if locator_value.get("element_id") else None,
                bbox=tuple(float(item) for item in bbox) if isinstance(bbox, list) else None,
                cell=dict(locator_value["cell"]) if isinstance(locator_value.get("cell"), Mapping) else None,
            )
        except (KeyError, TypeError, ValueError):
            return None
        measurements: list[Measurement] = []
        for item in value.get("measurements", []) if isinstance(value.get("measurements"), list) else []:
            if not isinstance(item, Mapping):
                continue
            raw = item.get("raw")
            if not isinstance(raw, str):
                continue
            number = item.get("value")
            measurements.append(Measurement(raw=raw, value=float(number) if isinstance(number, (int, float)) else None, unit=str(item["unit"]) if item.get("unit") else None, kind=str(item.get("kind") or "reported_value")))
        return ExperimentEvidence(kind=str(value.get("kind") or "unknown"), text=str(value.get("text") or locator.quote), locator=locator, measurements=tuple(measurements), comparator=str(value["comparator"]) if value.get("comparator") else None, quality=str(value.get("quality") or "unknown"))

    def values(field: str) -> tuple[ExperimentEvidence, ...]:
        raw = snapshot.get(field)
        return tuple(item for value in raw if (item := evidence(value)) is not None) if isinstance(raw, list) else ()

    associations: list[FigureTableAssociation] = []
    for value in snapshot.get("figure_table_refs", []) if isinstance(snapshot.get("figure_table_refs"), list) else []:
        if not isinstance(value, Mapping):
            continue
        try:
            associations.append(FigureTableAssociation(
                page=int(value["page"]), target_element_id=str(value["target_element_id"]) if value.get("target_element_id") else None,
                caption_element_id=str(value["caption_element_id"]) if value.get("caption_element_id") else None,
                target_kind=str(value["target_kind"]), label=str(value["label"]) if value.get("label") else None,
                caption=str(value["caption"]), relation=str(value["relation"]), confidence=str(value["confidence"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    required = ("paper_id", "source_version_id", "content_hash", "model", "prompt_version", "cache_key")
    if any(not isinstance(snapshot.get(field), str) or not str(snapshot.get(field)).strip() for field in required):
        raise ExperimentAnalysisError("invalid_cached_profile", "cached experiment profile is invalid")
    return ExperimentProfile(
        paper_id=str(snapshot["paper_id"]), source_version_id=str(snapshot["source_version_id"]), content_hash=str(snapshot["content_hash"]),
        model=str(snapshot["model"]), prompt_version=str(snapshot["prompt_version"]), cache_key=str(snapshot["cache_key"]),
        review_status=str(snapshot.get("review_status") or "review_pending"),
        **{field: values(field) for field in _PROFILE_EVIDENCE_FIELDS}, figure_table_refs=tuple(associations),
    )


def apply_verified_proposals(
    profile: ExperimentProfile, proposals: Sequence[Mapping[str, object]], pages: Mapping[int, str], elements: Sequence[DocumentElement],
) -> tuple[ExperimentProfile, int, int]:
    """Add only source-exact LLM proposals; invalid candidates are discarded.

    The proposal schema is intentionally small: category, page, quote, and an
    optional comparator.  Measurements and units are *derived from the quote*
    by :func:`evidence_from_quote`, never accepted from an LLM JSON value.
    """

    by_page = {element.page: [] for element in elements}
    for element in elements:
        by_page.setdefault(element.page, []).append(element)
    updated = {field: list(getattr(profile, field)) for field in _PROFILE_EVIDENCE_FIELDS}
    accepted = rejected = 0
    for proposal in proposals:
        category = proposal.get("category")
        quote = proposal.get("quote")
        page = proposal.get("page")
        comparator = proposal.get("comparator")
        if category not in _PROFILE_EVIDENCE_FIELDS or not isinstance(quote, str) or not isinstance(page, int) or page not in pages:
            rejected += 1
            continue
        if comparator is not None and (not isinstance(comparator, str) or not _valid_comparator(quote, comparator)):
            rejected += 1
            continue
        required_section = "results" if category == "observations" else "discussion" if category == "author_interpretations" else None
        if required_section and _section_for_quote(pages[page], quote) != required_section:
            rejected += 1
            continue
        # Proposals are page-text claims.  Table/figure-derived findings remain
        # deterministic because their coordinates/cells must be extracted first.
        try:
            item = evidence_from_quote(page=page, quote=quote, page_text=pages[page], kind=category, comparator=comparator)
        except ExperimentAnalysisError:
            rejected += 1
            continue
        if category in {"observations", "sample_sizes"} and not item.measurements:
            rejected += 1
            continue
        key = (item.locator.page, item.text, item.kind, item.comparator)
        if any((current.locator.page, current.text, current.kind, current.comparator) == key for current in updated[category]):
            continue
        updated[category].append(item)
        accepted += 1
    return replace(profile, **{field: tuple(values) for field, values in updated.items()}), accepted, rejected
