from app.models import Paper
from app.rag import chunk_pages, citations_from, research_gaps, search


def test_search_returns_grounded_chunk():
    paper = Paper(user_id="u", workspace_id="w", created_by="u-id", title="Transformer Study")
    paper.chunks = chunk_pages([(3, "Transformer models improve document retrieval accuracy.")], paper.id)
    results = search([paper], "document retrieval", 5)
    citations = citations_from(results)
    assert citations
    assert citations[0].page == 3
    assert citations[0].paper_title == "Transformer Study"
    assert citations[0].chunk_id == paper.chunks[0].id


def test_gap_extraction():
    paper = Paper(user_id="u", workspace_id="w", created_by="u-id", title="Study")
    paper.chunks = chunk_pages([(7, "A limitation is the small dataset. Future work should validate larger cohorts.")], paper.id)
    gaps = research_gaps([paper])
    assert len(gaps) == 1
    assert gaps[0]["page"] == "7"
