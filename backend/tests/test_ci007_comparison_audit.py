from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Principal
from app.store import PaperStore

def test_saved_comparison_retains_audit_snapshot():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    user, workspace = store.ensure_user(Principal(issuer="test", subject="audit"))
    saved = store.save_comparison(workspace.id, user.id, "Audit", ["paper"], [{"paper_id": "paper", "evidence_status": "unresolved"}], citation_snapshot=[{"source_span_id": "span", "quote": "text"}], human_judgment="held", judgment_reason="needs review")
    assert saved.citation_snapshot[0]["source_span_id"] == "span"
    loaded = store.list_comparisons(workspace.id)[0]
    assert loaded.human_judgment == "held" and loaded.judgment_reason == "needs review"
