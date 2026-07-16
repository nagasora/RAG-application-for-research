import hashlib
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, PaperRecord
from app.models import Chunk, Citation, KnowledgeNode, Paper, Principal, SourceSpan, SourceVersion
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'rag-graph.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: LocalOriginalStorage(
        tmp_path / "originals",
    )
    return store


def _paper(paper_id="paper", chunk_id="chunk", text="strong paper evidence"):
    return Paper(
        id=paper_id, user_id="user", workspace_id="workspace", created_by="user",
        title="Paper", content_hash="a" * 64,
        chunks=[Chunk(id=chunk_id, paper_id=paper_id, page=1, text=text)],
    )


def _citation(chunk_id, score, **updates):
    base = Citation(
        index=1, paper_id="paper", paper_title="Paper", chunk_id=chunk_id,
        page=1, section="Results", excerpt="evidence", score=score,
    )
    return base.model_copy(update=updates)


def test_rrf_deduplicates_shared_chunk_and_breaks_channel_ties_by_relevance():
    strong = _citation("strong", 0.95)
    weak = _citation(
        "weak", 0.05, source_kind="graph_node", knowledge_node_id="node",
    )
    shared_graph = _citation(
        "strong", 0.40, source_kind="graph_edge", evidence_role="contradicts",
        knowledge_edge_id="edge",
    )
    fused = main._fused_retrieval_citations(
        [strong],
        {"graph_nodes": [("node:weak", weak)], "graph_contradicts": [("edge", shared_graph)]},
        8,
    )
    assert [item.chunk_id for item in fused] == ["strong", "weak"]
    assert fused[0].source_kind == "graph_edge"
    assert set(fused[0].retrieval_channels) == {"paper", "graph_contradicts"}


def test_graph_seed_preserves_explicit_zero_confidence(monkeypatch):
    captured = []
    node = KnowledgeNode(
        id="node", workspace_id="workspace", node_type="hypothesis", status="active",
        layer=0, content="scope token", phase="test", confidence=0.0,
        created_at="2026-07-16T00:00:00+00:00", updated_at="2026-07-16T00:00:00+00:00",
    )

    class Store:
        def list_knowledge_nodes(self, workspace_id):
            return [node]

        def list_knowledge_edges(self, workspace_id):
            return []

    def capture(workspace_id, seeds, neighbors, config):
        captured.extend(seeds)
        return []

    monkeypatch.setattr(main, "pruned_two_hop_retrieve", capture)
    main._graph_citation_candidates(Store(), "workspace", "scope token", [_paper()], 8)
    assert captured[0].confidence == 0.0


def test_graph_failure_falls_back_to_paper_and_zero_overlap_span_is_rejected():
    class BrokenGraphStore:
        def list_knowledge_nodes(self, workspace_id):
            raise RuntimeError("database detail must stay private")

    assert main._safe_graph_citation_candidates(
        BrokenGraphStore(), "workspace", "query", [_paper()], 8,
    ) == {"graph_nodes": [], "graph_contradicts": []}
    paper_citation = _citation("chunk", 0.9)
    fused = main._fused_retrieval_citations(
        [paper_citation], {"graph_nodes": [], "graph_contradicts": []}, 8,
    )
    assert [item.chunk_id for item in fused] == ["chunk"]
    assert main._best_span_chunk(_paper(text="completely unrelated"), "zero overlap quote", 1) is None


def test_negative_graph_path_keeps_evidence_role_and_challenge_uses_stance():
    paper = _paper(text="supporting source quote")
    version = SourceVersion(
        id="version", workspace_id="workspace", paper_id=paper.id, kind="paper",
        locator="paper:paper", content_hash="a" * 64,
        created_at="2026-07-16T00:00:00+00:00",
    )
    span = SourceSpan(
        id="span", workspace_id="workspace", source_version_id=version.id,
        page=1, text="supporting source quote",
        created_at="2026-07-16T00:00:00+00:00",
    )

    class Store:
        def get_source_version(self, workspace_id, source_version_id):
            return version

        def get_source_span(self, workspace_id, source_span_id):
            return span

    evidence = SimpleNamespace(
        source_version_id=version.id, source_span_id=span.id,
        verbatim_quote=span.text, role="supports", extraction_quality="high",
    )
    citation = main._paper_backed_graph_citation(
        Store(), "workspace", {paper.id: paper}, evidence=evidence,
        source_kind="graph_node", score=0.7, knowledge_node_id="node",
        graph_path=[{"relation": "contradicts"}], retrieval_stance="negative",
    )
    assert citation.evidence_role == "supports"
    assert citation.retrieval_stance == "negative"
    appendix, claims, _ = main._mode_claims(
        main.SearchRequest(query="challenge", interaction_mode="challenge"),
        [citation.model_copy(update={"index": 1})], [],
    )
    assert "unverified" not in appendix
    assert claims[1]["classification"] == "evidence_backed"


def test_same_chunk_high_score_graph_candidate_keeps_matching_provenance():
    weak = _citation(
        "shared", 0.2, source_kind="graph_edge", knowledge_edge_id="weak",
        source_quote="weak quote", retrieval_reason="weak",
    )
    strong = _citation(
        "shared", 0.9, source_kind="graph_edge", knowledge_edge_id="strong",
        source_quote="strong quote", retrieval_reason="strong",
    )
    fused = main._fused_retrieval_citations(
        [], {"graph_nodes": [], "graph_contradicts": [("weak", weak), ("strong", strong)]}, 8,
    )
    assert len(fused) == 1
    assert fused[0].score == 0.9
    assert fused[0].knowledge_edge_id == "strong"
    assert fused[0].source_quote == "strong quote"
    assert fused[0].retrieval_reason == "strong"


def test_direct_negative_node_evidence_is_used_by_challenge():
    citation = _citation(
        "negative", 0.8, source_kind="graph_node", evidence_role="contradicts",
        retrieval_stance="negative", knowledge_node_id="negative-node",
        graph_path=[], retrieval_channels=["graph_nodes"],
    )
    appendix, claims, _ = main._mode_claims(
        main.SearchRequest(query="challenge", interaction_mode="challenge"),
        [citation], [],
    )
    assert "unverified" not in appendix
    assert claims[1]["classification"] == "evidence_backed"


def test_normal_search_fuses_paper_graph_and_negative_evidence_with_provenance(
    tmp_path, monkeypatch,
):
    store = _setup(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    headers = {"X-Dev-User": "alice"}
    try:
        with TestClient(main.app) as client:
            uploaded = client.post(
                "/api/papers/upload", headers=headers,
                files={
                    "files": (
                        "target.txt",
                        b"Target treatment improves outcome. Control evidence shows target treatment does not improve outcome.",
                        "text/plain",
                    ),
                },
            )
            assert uploaded.status_code == 200
            paper_id = uploaded.json()[0]["paper"]["id"]

            alice, workspace = store.ensure_user(
                Principal(issuer="paperpilot-dev", subject="alice"),
            )
            version = next(
                item for item in store.list_source_versions(workspace.id, "paper")
                if item.paper_id == paper_id
            )
            span = store.list_source_spans(workspace.id, version.id)[0]
            supporting = store.create_knowledge_node(
                workspace.id, node_type="source",
                content="target treatment improves outcome", status="active",
                created_by=alice.id, evidence_span_ids=[span.id],
            )
            negative = store.create_knowledge_node(
                workspace.id, node_type="hypothesis",
                content="target treatment does not improve outcome", status="active",
                created_by=alice.id, evidence_span_ids=[span.id],
            )
            edge = store.create_knowledge_edge(
                workspace.id, source_node_id=negative.id,
                target_node_id=supporting.id, relation="contradicts",
                evidence_span_ids=[span.id], created_by=alice.id,
                metadata={"confidence": 0.95},
            )

            response = client.post(
                "/api/search", headers=headers,
                json={
                    "query": "target treatment outcome", "paper_ids": [paper_id],
                    "interaction_mode": "challenge", "limit": 8,
                },
            )

        assert response.status_code == 200
        payload = response.json()
        contradiction = next(
            item for item in payload["citations"]
            if item["knowledge_edge_id"] == edge.id
        )
        assert contradiction["paper_id"] == paper_id
        assert contradiction["source_kind"] == "graph_edge"
        assert contradiction["source_version_id"] == version.id
        assert contradiction["source_span_id"] == span.id
        assert contradiction["evidence_role"] == "supports"
        assert contradiction["retrieval_stance"] == "negative"
        assert contradiction["graph_path"][0]["relation"] == "contradicts"
        assert "graph_contradicts" in contradiction["retrieval_channels"]
        assert "paper" in contradiction["retrieval_channels"]
        assert contradiction["fusion_score"] is not None
        assert contradiction["chunk_id"] in {
            chunk.id for chunk in store.get_owned(workspace.id, paper_id).chunks
        }
        assert len({item["chunk_id"] for item in payload["citations"]}) == len(payload["citations"])
        assert "unverified" not in payload["answer"]
        assert any(
            claim["classification"] == "evidence_backed"
            and contradiction["index"] in claim["citation_ids"]
            for claim in payload["claims"]
        )
        # Old clients still receive every original required field unchanged.
        assert {
            "index", "paper_id", "paper_title", "chunk_id", "page",
            "section", "excerpt", "score",
        } <= set(contradiction)
    finally:
        main.app.dependency_overrides.clear()


def test_graph_retrieval_respects_workspace_and_year_scope(tmp_path, monkeypatch):
    store = _setup(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        with TestClient(main.app) as client:
            uploaded = client.post(
                "/api/papers/upload", headers={"X-Dev-User": "alice"},
                files={"files": ("scope.txt", b"scope token evidence", "text/plain")},
            ).json()[0]["paper"]
            alice, workspace = store.ensure_user(
                Principal(issuer="paperpilot-dev", subject="alice"),
            )
            version = next(
                item for item in store.list_source_versions(workspace.id, "paper")
                if item.paper_id == uploaded["id"]
            )
            span = store.list_source_spans(workspace.id, version.id)[0]
            store.create_knowledge_node(
                workspace.id, node_type="source", content="scope token evidence",
                status="active", created_by=alice.id, evidence_span_ids=[span.id],
            )

            bob, bob_workspace = store.ensure_user(
                Principal(issuer="paperpilot-dev", subject="bob"),
            )
            content = "scope token foreign secret"
            _, bob_spans = store.create_source_import(
                bob_workspace.id, kind="markdown", locator="note://foreign",
                content_hash=hashlib.sha256(content.encode()).hexdigest(), metadata={},
                spans=[{"page": 1, "text": content}],
            )
            foreign = store.create_knowledge_node(
                bob_workspace.id, node_type="source", content=content, status="active",
                created_by=bob.id, evidence_span_ids=[bob_spans[0].id],
            )

            allowed = client.post(
                "/api/search", headers={"X-Dev-User": "alice"},
                json={"query": "scope token", "paper_ids": [uploaded["id"]]},
            )
            with store.session_factory.begin() as session:
                session.get(PaperRecord, uploaded["id"]).year = 2020
            excluded_by_year = client.post(
                "/api/search", headers={"X-Dev-User": "alice"},
                json={
                    "query": "scope token", "paper_ids": [uploaded["id"]],
                    "year_from": 2021,
                },
            )

        assert allowed.status_code == 200
        assert all(
            citation.get("knowledge_node_id") != foreign.id
            for citation in allowed.json()["citations"]
        )
        assert excluded_by_year.status_code == 200
        assert excluded_by_year.json()["citations"] == []
    finally:
        main.app.dependency_overrides.clear()
