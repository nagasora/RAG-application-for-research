import json

from app.generation_settings import GenerationSelection
from app.mind_map_generation import generate_candidate


def test_missing_provider_key_uses_manual_safe_candidate(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = generate_candidate(
        GenerationSelection("openai", "gpt-5.6-luna"),
        title="研究テーマ",
        source_text="原文",
        source_scope={"source_span_ids": ["span-1"]},
    )
    assert result.attempted is False
    assert result.nodes[0]["kind"] == "root"
    assert result.nodes[0]["source_span_ids"] == []


def test_gemini_candidate_accepts_only_allowlisted_evidence(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    payload = {"nodes": [
        {"kind": "root", "title": "研究テーマ", "body": "要約", "evidence_ref_ids": [], "source_span_ids": ["span-1"]},
        {"kind": "finding", "title": "知見", "body": "原文に基づく", "evidence_ref_ids": [], "source_span_ids": ["span-1"]},
    ]}
    monkeypatch.setattr("app.gemini_gateway.generate_json", lambda *args: json.dumps(payload))
    result = generate_candidate(
        GenerationSelection("gemini", "gemini-3.5-flash-lite"),
        title="研究テーマ",
        source_text="原文",
        source_scope={"source_span_ids": ["span-1"], "evidence_ref_ids": []},
    )
    assert result.attempted is True
    assert result.fallback_reason is None
    assert result.nodes[1]["source_span_ids"] == ["span-1"]


def test_invalid_json_and_out_of_scope_evidence_are_distinguished(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("app.gemini_gateway.generate_json", lambda *args: "")
    invalid = generate_candidate(
        GenerationSelection("gemini", "gemini-3.5-flash-lite"),
        title="研究テーマ",
        source_text="原文",
        source_scope={"source_span_ids": ["span-1"]},
    )
    assert invalid.attempted is True
    assert invalid.fallback_reason == "generation_failed"

    payload = {"nodes": [
        {"kind": "root", "title": "研究テーマ", "body": "", "evidence_ref_ids": [], "source_span_ids": ["outside"]},
    ]}
    monkeypatch.setattr("app.gemini_gateway.generate_json", lambda *args: json.dumps(payload))
    outside = generate_candidate(
        GenerationSelection("gemini", "gemini-3.5-flash-lite"),
        title="研究テーマ",
        source_text="原文",
        source_scope={"source_span_ids": ["span-1"]},
    )
    assert outside.fallback_reason == "out_of_scope_evidence"
