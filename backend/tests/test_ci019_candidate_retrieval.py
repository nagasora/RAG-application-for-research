from fastapi.testclient import TestClient
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import (
    Base, EvidenceRefRecord, KnowledgeEdgeRecord, KnowledgeNodeRecord, SourceSpanRecord,
    SourceVersionRecord,
)
from app.models import Chunk, Paper, Principal
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ci019.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    return store, engine


def _paper(user, workspace, paper_id, *, year=2024, chunks=1, text="bounded retrieval evidence"):
    return Paper(
        id=paper_id, user_id=user.subject, workspace_id=workspace.id,
        created_by=user.id, title=f"Paper {paper_id}", year=year,
        status="ready", content_hash=(paper_id[0] * 64),
        chunks=[Chunk(
            id=f"{paper_id}-chunk-{index}", paper_id=paper_id, page=index + 1,
            text=f"{text} {index}",
        ) for index in range(chunks)],
    )


def test_candidate_query_is_scoped_bounded_and_has_portable_fallback(tmp_path):
    store, _ = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="test", subject="alice"))
    bob, bob_workspace = store.ensure_user(Principal(issuer="test", subject="bob"))
    store.upsert(_paper(alice, workspace, "a-paper", chunks=250))
    store.upsert(_paper(alice, workspace, "b-paper", year=2018, text="foreign year token"))
    store.upsert(_paper(bob, bob_workspace, "c-paper", text="foreign workspace secret"))

    bounded = store.search_chunk_candidates(workspace.id, "bounded retrieval", limit=20)
    bounded_chunks = [chunk for paper in bounded for chunk in paper.chunks]
    assert len(bounded_chunks) == 80
    assert {paper.id for paper in bounded} == {"a-paper"}

    scoped = store.search_chunk_candidates(
        workspace.id, "foreign", limit=8, paper_ids=["b-paper", "c-paper"],
        year_from=2020,
    )
    assert scoped == []

    fallback = store.search_chunk_candidates(workspace.id, "!?", limit=8)
    assert 0 < sum(len(paper.chunks) for paper in fallback) <= 32


def test_search_rejects_more_than_500_selected_papers(tmp_path):
    _setup(tmp_path)
    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/api/search", headers={"X-Dev-User": "alice"},
                json={"query": "bounded query", "paper_ids": [f"paper-{index}" for index in range(501)]},
            )
        assert response.status_code == 422
    finally:
        main.app.dependency_overrides.clear()


def test_answer_does_not_hydrate_workspace_and_timing_never_logs_query(tmp_path, monkeypatch):
    store, _ = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
    store.upsert(_paper(alice, workspace, "a-paper", chunks=40, text="private-query-token evidence"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(store, "list", lambda *_: (_ for _ in ()).throw(AssertionError("must not hydrate workspace")))
    timing_logs = []
    original_info = main.logger.info

    def capture(message, *args, **kwargs):
        if str(message).startswith("rag_query_stage"):
            timing_logs.append((message, args))
        else:
            original_info(message, *args, **kwargs)

    monkeypatch.setattr(main.logger, "info", capture)
    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/api/search", headers={"X-Dev-User": "alice"},
                json={"query": "private-query-token", "paper_ids": ["a-paper"]},
            )
        assert response.status_code == 200
        assert response.json()["citations"]
        assert timing_logs
        assert "private-query-token" not in repr(timing_logs)
    finally:
        main.app.dependency_overrides.clear()


def test_graph_provenance_resolution_has_constant_query_bound(tmp_path):
    store, engine = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="test", subject="alice"))
    paper = _paper(alice, workspace, "a-paper", text="graph scope evidence")
    store.upsert(paper)
    now = datetime.now(timezone.utc)
    with store.session_factory.begin() as session:
        session.add(SourceVersionRecord(
            id="version", workspace_id=workspace.id, paper_id=paper.id,
            kind="paper", locator=f"paper:{paper.id}", content_hash=paper.content_hash,
            metadata_json={}, created_at=now,
        ))
        session.flush()
        session.add(SourceSpanRecord(
            id="span", workspace_id=workspace.id, source_version_id="version",
            page=1, text="graph scope evidence 0", locator_json={}, created_at=now,
        ))
    source = store.create_knowledge_node(
        workspace.id, node_type="source", content="graph scope evidence",
        status="active", created_by=alice.id, evidence_span_ids=["span"],
    )
    hypothesis = store.create_knowledge_node(
        workspace.id, node_type="hypothesis", content="graph scope alternative",
        status="active", created_by=alice.id, evidence_span_ids=["span"],
    )
    store.create_knowledge_edge(
        workspace.id, source_node_id=hypothesis.id, target_node_id=source.id,
        relation="contradicts", created_by=alice.id, evidence_span_ids=["span"],
    )
    statements = []

    def count(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        result = main._graph_citation_candidates(store, workspace.id, "graph scope", [paper], 8)
    finally:
        event.remove(engine, "before_cursor_execute", count)
        main.app.dependency_overrides.clear()

    assert result["graph_nodes"]
    assert result["graph_contradicts"]
    # Workspace check + nodes/evidence + workspace check + edges/evidence +
    # batched source versions/spans. The bound does not grow with hit count.
    assert len(statements) <= 11


def test_graph_rows_and_material_ids_are_hard_bounded(tmp_path, monkeypatch):
    store, _ = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="test", subject="alice"))
    now = datetime.now(timezone.utc)
    with store.session_factory.begin() as session:
        session.add_all([KnowledgeNodeRecord(
            id=f"node-{index}", workspace_id=workspace.id, created_by=alice.id,
            node_type="hypothesis", status="active", layer=0,
            content=f"bounded graph query {index}", phase="test", confidence=1.0,
            metadata_json={}, created_at=now, updated_at=now,
        ) for index in range(40)])
        session.flush()
        session.add_all([KnowledgeEdgeRecord(
            id=f"edge-{index}", workspace_id=workspace.id,
            source_node_id=f"node-{index % 40}", target_node_id=f"node-{(index + 1) % 40}",
            relation="related", status="active", origin="manual", created_by=alice.id,
            metadata_json={}, created_at=now, updated_at=now,
        ) for index in range(250)])

    nodes, edges = store.retrieve_knowledge_subgraph(
        workspace.id, "bounded graph query", seed_limit=12, edge_limit=200,
        evidence_limit=400,
    )
    assert len(nodes) <= 412
    assert len(edges) <= 200


def test_graph_page_chunk_is_loaded_independently_of_lexical_pool(tmp_path, monkeypatch):
    store, _ = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="test", subject="alice"))
    paper = _paper(alice, workspace, "a-paper", chunks=40, text="lexical pool filler")
    paper.chunks[-1].text = "page forty exact graph evidence"
    store.upsert(paper)
    now = datetime.now(timezone.utc)
    with store.session_factory.begin() as session:
        session.add(SourceVersionRecord(
            id="page40-version", workspace_id=workspace.id, paper_id=paper.id,
            kind="paper", locator=f"paper:{paper.id}", content_hash=paper.content_hash,
            metadata_json={}, created_at=now,
        ))
        session.flush()
        session.add(SourceSpanRecord(
            id="page40-span", workspace_id=workspace.id, source_version_id="page40-version",
            page=40, text="page forty exact graph evidence", locator_json={}, created_at=now,
        ))
    store.create_knowledge_node(
        workspace.id, node_type="source", content="semantic graph target",
        status="active", created_by=alice.id, evidence_span_ids=["page40-span"],
    )
    coarse = store.search_chunk_candidates(workspace.id, "lexical pool filler", limit=8)
    assert all(chunk.page != 40 for item in coarse for chunk in item.chunks)
    captured = []
    original = store.get_source_materials

    def bounded_materials(workspace_id, version_ids, span_ids):
        captured.append((len(version_ids), len(span_ids)))
        return original(workspace_id, version_ids, span_ids)

    monkeypatch.setattr(store, "get_source_materials", bounded_materials)
    result = main._graph_citation_candidates(
        store, workspace.id, "semantic graph target", coarse, 8,
        paper_ids=[paper.id],
    )
    citation = result["graph_nodes"][0][1]
    assert citation.page == 40
    assert citation.chunk_id == paper.chunks[-1].id
    assert captured and captured[0][0] <= 400 and captured[0][1] <= 400


def test_graph_seed_preserves_evidence_only_match_beyond_content_cap(tmp_path):
    store, _ = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="test", subject="alice"))
    now = datetime.now(timezone.utc)
    with store.session_factory.begin() as session:
        session.add(SourceVersionRecord(
            id="seed-version", workspace_id=workspace.id, paper_id=None,
            kind="markdown", locator="memory://seed", content_hash="d" * 64,
            metadata_json={}, created_at=now,
        ))
        session.flush()
        session.add(SourceSpanRecord(
            id="seed-span", workspace_id=workspace.id, source_version_id="seed-version",
            page=1, text="rare evidence-only phrase", locator_json={}, created_at=now,
        ))
    for index in range(12):
        store.create_knowledge_node(
            workspace.id, node_type="hypothesis", content=f"generic node {index:02d}",
            status="active", created_by=alice.id,
        )
    target = store.create_knowledge_node(
        workspace.id, node_type="hypothesis", content="generic node zz",
        status="active", created_by=alice.id, evidence_span_ids=["seed-span"],
    )
    nodes, _ = store.retrieve_knowledge_subgraph(
        workspace.id, "rare evidence-only phrase", seed_limit=12,
    )
    assert target.id in {node.id for node in nodes}


def test_graph_uses_one_evidence_budget_and_preserves_contradiction(tmp_path):
    store, _ = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="test", subject="alice"))
    now = datetime.now(timezone.utc)
    with store.session_factory.begin() as session:
        session.add(SourceVersionRecord(
            id="budget-version", workspace_id=workspace.id, paper_id=None,
            kind="markdown", locator="memory://budget", content_hash="e" * 64,
            metadata_json={}, created_at=now,
        ))
        session.flush()
        session.add(SourceSpanRecord(
            id="budget-span", workspace_id=workspace.id, source_version_id="budget-version",
            page=1, text="budget query evidence", locator_json={}, created_at=now,
        ))
    source = store.create_knowledge_node(
        workspace.id, node_type="hypothesis", content="budget query source",
        status="active", created_by=alice.id,
    )
    alternative = store.create_knowledge_node(
        workspace.id, node_type="hypothesis", content="budget query alternative",
        status="active", created_by=alice.id,
    )
    related = store.create_knowledge_edge(
        workspace.id, source_node_id=source.id, target_node_id=alternative.id,
        relation="related", created_by=alice.id, evidence_span_ids=["budget-span"],
    )
    contradiction = store.create_knowledge_edge(
        workspace.id, source_node_id=alternative.id, target_node_id=source.id,
        relation="contradicts", created_by=alice.id, evidence_span_ids=["budget-span"],
    )
    with store.session_factory.begin() as session:
        def evidence(ref_id, *, node_id=None, edge_id=None, role="supports"):
            return EvidenceRefRecord(
                id=ref_id, workspace_id=workspace.id, source_span_id="budget-span",
                knowledge_node_id=node_id, knowledge_edge_id=edge_id,
                source_version_id="budget-version", target_claim="budget query",
                role=role, extraction_quality="high", quote_start=0,
                quote_end=len("budget query evidence"),
                verbatim_quote="budget query evidence", excerpt="", created_at=now,
            )
        session.add_all([
            evidence(f"node-ref-{index:04d}", node_id=source.id) for index in range(450)
        ])
        session.add_all([
            evidence(f"a-related-ref-{index:04d}", edge_id=related.id) for index in range(250)
        ])
        session.add(evidence(
            "z-contradiction-ref", edge_id=contradiction.id, role="contradicts",
        ))
    nodes, edges = store.retrieve_knowledge_subgraph(
        workspace.id, "budget query", evidence_limit=400,
    )
    assert sum(len(node.evidence) for node in nodes) + sum(len(edge.evidence) for edge in edges) <= 400
    selected = next(edge for edge in edges if edge.id == contradiction.id)
    assert selected.evidence
