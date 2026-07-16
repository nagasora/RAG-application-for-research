from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import main
from app.database import Base
from app.store import PaperStore

def test_belief_ledger_is_append_only_and_rejected_is_not_positive_context(tmp_path):
 e=create_engine(f"sqlite:///{tmp_path/'b.db'}",connect_args={"check_same_thread":False});Base.metadata.create_all(e);store=PaperStore(session_factory=sessionmaker(bind=e,expire_on_commit=False));main.app.dependency_overrides[main.get_store]=lambda:store
 try:
  with TestClient(main.app) as c:
   h={"X-Dev-User":"alice"}; first=c.post("/api/beliefs",headers=h,json={"belief_key":"dose", "content":"dose improves outcome", "status":"supported", "reason":"trial"}); rejected=c.post("/api/beliefs",headers=h,json={"belief_key":"dose", "content":"dose was refuted", "status":"rejected", "reason":"replication"}); positive=c.get("/api/beliefs?query=outcome",headers=h)
  assert first.status_code==201 and rejected.status_code==201
  assert positive.json()==[]
 finally: main.app.dependency_overrides.clear()
