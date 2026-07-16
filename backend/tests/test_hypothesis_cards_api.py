from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base
from app.store import PaperStore


def test_hypothesis_card_requires_competitor_and_falsifier_before_reviewable(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cards.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            headers = {"X-Dev-User": "alice"}
            draft = client.post("/api/hypotheses", headers=headers, json={"claim": "Intervention improves outcome", "mechanism": "pathway", "target": "adults", "intervention": "dose", "outcome": "score", "direction": "increase", "test": "randomized trial"})
            assert draft.status_code == 201 and draft.json()["status"] == "draft"
            blocked = client.patch(f"/api/hypotheses/{draft.json()['id']}/status", headers=headers, json={"status": "reviewable"})
            assert blocked.status_code == 422
            complete = client.post("/api/hypotheses", headers=headers, json={"claim": "Intervention improves outcome", "mechanism": "pathway", "target": "adults", "conditions": "baseline risk", "intervention": "dose", "outcome": "score", "direction": "increase", "assumptions": ["adherence"], "competing_theories": ["selection explains outcome"], "predictions": ["score rises"], "falsifiers": ["no score change under dose"], "test": "randomized trial"})
            card_id = complete.json()["id"]
            reviewable = client.patch(f"/api/hypotheses/{card_id}/status", headers=headers, json={"status": "reviewable"})
            reviewed = client.patch(f"/api/hypotheses/{card_id}/status", headers=headers, json={"status": "reviewed", "human_reviewed": True})
            supported = client.patch(f"/api/hypotheses/{card_id}/status", headers=headers, json={"status": "supported", "empirically_supported": True})
        assert reviewable.status_code == 200
        assert reviewed.json()["human_reviewed"] is True and reviewed.json()["empirically_supported"] is False
        assert supported.json()["empirically_supported"] is True
    finally:
        main.app.dependency_overrides.clear()
