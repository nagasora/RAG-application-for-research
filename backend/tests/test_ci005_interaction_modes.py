import pytest
from fastapi import HTTPException

from app import main
from app.models import Citation, SearchRequest


def _citation():
    return Citation(index=1, paper_id="paper", paper_title="Paper", chunk_id="chunk", page=1, section="Results", excerpt="Observed evidence", score=1.0)


def test_evidence_mode_emits_only_selected_source_backed_claims():
    appendix, claims, draft = main._mode_claims(SearchRequest(query="what", paper_ids=["paper"], interaction_mode="evidence"), [_citation()], [])
    assert appendix == "" and not draft
    assert claims == [{"claim_id": "evidence-1", "text": "Observed evidence", "kind": "paper", "citation_ids": [1], "classification": "evidence_backed"}]


def test_explore_challenge_design_and_update_have_auditable_claim_classes():
    for mode, expected in [("explore", 3), ("challenge", 2), ("design", 1), ("update", 1)]:
        appendix, claims, draft = main._mode_claims(SearchRequest(query="what", interaction_mode=mode), [_citation()], [])
        assert draft and len(claims) == expected
        expected_classes = {"hypothesis", "unverified"} if mode == "challenge" else {"hypothesis"}
        assert {claim["classification"] for claim in claims} <= expected_classes
        assert all(not claim["citation_ids"] for claim in claims)
        assert appendix


def test_challenge_uses_retrieved_contradiction_as_evidence():
    contradiction = _citation().model_copy(update={
        "retrieval_channels": ["graph_contradicts"],
        "retrieval_stance": "negative",
    })
    appendix, claims, draft = main._mode_claims(
        SearchRequest(query="what", interaction_mode="challenge"), [contradiction], [],
    )
    assert draft and "unverified" not in appendix
    assert claims[1]["classification"] == "evidence_backed"
    assert claims[1]["citation_ids"] == [1]


def test_legacy_claim_kinds_are_classified_without_changing_kind():
    _, claims, _ = main._mode_claims(SearchRequest(query="what"), [_citation()], [
        {"claim_id": "p", "text": "paper", "kind": "paper", "citation_ids": [1]},
        {"claim_id": "g", "text": "general", "kind": "general", "citation_ids": []},
    ])
    assert [claim["classification"] for claim in claims] == ["evidence_backed", "general_knowledge"]


def test_evidence_mode_rejects_an_unscoped_request_before_retrieval():
    with pytest.raises(HTTPException, match="selected paper_ids"):
        main._answer(SearchRequest(query="what", interaction_mode="evidence"), object(), object())
