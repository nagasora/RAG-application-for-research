from app.models import Paper
from app.rag import chunk_pages, citations_from, embed_texts, embedding_config, extractive_answer, research_gaps, search


def test_local_embedding_provider_stays_local_even_when_openai_key_exists(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EMBEDDING_MODEL", "local-hash-v1")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    assert embedding_config() == ("local", "local-hash-v1")
    vectors = embed_texts(["local deterministic vector"])
    assert len(vectors) == 1 and len(vectors[0]) == 384


def test_search_returns_grounded_chunk():
    paper = Paper(user_id="u", workspace_id="w", created_by="u-id", title="Transformer Study")
    paper.chunks = chunk_pages([(3, "Transformer models improve document retrieval accuracy.")], paper.id)
    results = search([paper], "document retrieval", 5)
    citations = citations_from(results)
    assert citations
    assert citations[0].page == 3
    assert citations[0].paper_title == "Transformer Study"
    assert citations[0].chunk_id == paper.chunks[0].id


def test_extractive_fallback_does_not_misreport_api_key_as_unset():
    paper = Paper(user_id="u", workspace_id="w", created_by="u-id", title="Retrieval Study")
    paper.chunks = chunk_pages([(1, "Document retrieval improves with grounded evidence.")], paper.id)
    answer = extractive_answer("retrieval", citations_from(search([paper], "retrieval", 1)))
    assert "OPENAI_API_KEYが未設定" not in answer
    assert "LLM生成を利用できなかった" in answer


def test_gap_extraction():
    paper = Paper(user_id="u", workspace_id="w", created_by="u-id", title="Study")
    paper.chunks = chunk_pages([(7, "A limitation is the small dataset. Future work should validate larger cohorts.")], paper.id)
    gaps = research_gaps([paper])
    assert len(gaps) == 1
    assert gaps[0]["page"] == "7"
