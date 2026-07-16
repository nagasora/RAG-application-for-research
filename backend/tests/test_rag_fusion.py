from app.rag import reciprocal_rank_fusion

def test_rrf_fuses_paper_support_and_graph_contradiction_without_losing_negative_evidence():
    fused=reciprocal_rank_fusion({"paper":["support","negative"],"graph_contradicts":["negative"]})
    ids=[item[0] for item in fused]
    negative=next(item for item in fused if item[0]=="negative")
    assert ids[0]=="negative" and "graph_contradicts" in negative[2] and "paper" in negative[2]
