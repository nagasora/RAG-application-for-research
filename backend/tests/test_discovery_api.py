import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import main
from app.database import Base
from app.semantic_scholar import fetch_paper
from app.store import PaperStore

def test_discovery_queue_is_review_gated_and_provider_is_mocked(tmp_path):
    response = httpx.Response(200, json={"paperId":"s2", "title":"New paper", "abstract":"Quoted source", "url":"https://example.test/p"})
    client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
    snapshot = fetch_paper("s2", client)
    engine = create_engine(f"sqlite:///{tmp_path/'d.db'}", connect_args={"check_same_thread":False}); Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False)); main.app.dependency_overrides[main.get_store] = lambda: store
    try:
      with TestClient(main.app) as api:
        h={"X-Dev-User":"alice"}; created=api.post("/api/discovery/items",headers=h,json={"provider_paper_id":"s2","classification":"contradicts","title":snapshot["title"],"abstract":snapshot["abstract"],"source_quote":"Quoted source","source_url":snapshot["url"],"license":"semantic_scholar_api","snapshot":snapshot})
        queue=api.get("/api/discovery/review-queue",headers=h); reviewed=api.patch(f"/api/discovery/items/{created.json()['id']}/review",headers=h,json={"review_status":"accepted"})
      assert created.status_code==201 and created.json()["review_status"]=="pending"
      assert queue.json()[0]["source_quote"]=="Quoted source" and reviewed.json()["review_status"]=="accepted"
    finally: main.app.dependency_overrides.clear(); client.close()
