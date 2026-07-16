from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base
from app.models import Paper, Principal
from app.store import PaperStore


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'research-assets.db'}", connect_args={"check_same_thread": False})
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    Base.metadata.create_all(engine)
    main.app.dependency_overrides[main.get_store] = lambda: store
    return store


def _headers(user, workspace_id=None):
    values = {"X-Dev-User": user}
    if workspace_id:
        values["X-Workspace-ID"] = workspace_id
    return values


def _paper(store, workspace_id, user_id, title, content_hash):
    return store.upsert(Paper(
        user_id=user_id, workspace_id=workspace_id, created_by=user_id,
        title=title, content_hash=content_hash,
    ))


def test_research_questions_and_source_sets_are_workspace_scoped_crud(tmp_path):
    store = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
    bob, bob_workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
    first = _paper(store, workspace.id, alice.id, "First", "a" * 64)
    second = _paper(store, workspace.id, alice.id, "Second", "b" * 64)
    outsider = _paper(store, bob_workspace.id, bob.id, "Outsider", "c" * 64)
    try:
        with TestClient(main.app) as client:
            question = client.post("/api/research/questions", headers=_headers("alice"), json={
                "title": "Retrieval", "question": "Which retrieval method remains reliable?",
            })
            assert question.status_code == 201
            question_id = question.json()["id"]
            listed_questions = client.get("/api/research/questions", headers=_headers("alice"))
            changed_question = client.patch(
                f"/api/research/questions/{question_id}", headers=_headers("alice"),
                json={"question": "Which retrieval method remains reliable under shift?"},
            )

            source_set = client.post("/api/source-sets", headers=_headers("alice"), json={
                "name": "Core evidence", "description": "Initial papers", "paper_ids": [second.id, first.id],
            })
            assert source_set.status_code == 201
            source_set_id = source_set.json()["id"]
            changed_set = client.patch(
                f"/api/source-sets/{source_set_id}", headers=_headers("alice"),
                json={"name": "Updated evidence", "paper_ids": [first.id]},
            )
            invalid_update = client.patch(
                f"/api/source-sets/{source_set_id}", headers=_headers("alice"),
                json={"name": "Must roll back", "paper_ids": [outsider.id]},
            )
            unchanged_after_invalid_update = client.get(
                f"/api/source-sets/{source_set_id}", headers=_headers("alice"),
            )
            invalid_scope = client.post("/api/source-sets", headers=_headers("alice"), json={
                "name": "Invalid", "paper_ids": [outsider.id],
            })
            duplicate = client.post("/api/source-sets", headers=_headers("alice"), json={
                "name": "Duplicate", "paper_ids": [first.id, first.id],
            })
            deleted_question = client.delete(f"/api/research/questions/{question_id}", headers=_headers("alice"))
            deleted_set = client.delete(f"/api/source-sets/{source_set_id}", headers=_headers("alice"))

        assert listed_questions.status_code == 200 and listed_questions.json()[0]["id"] == question_id
        assert changed_question.status_code == 200 and changed_question.json()["question"].endswith("shift?")
        assert source_set.json()["paper_ids"] == [second.id, first.id]
        assert changed_set.status_code == 200 and changed_set.json()["paper_ids"] == [first.id]
        assert invalid_update.status_code == 404
        assert unchanged_after_invalid_update.json()["name"] == "Updated evidence"
        assert unchanged_after_invalid_update.json()["paper_ids"] == [first.id]
        assert invalid_scope.status_code == 404 and duplicate.status_code == 422
        assert deleted_question.status_code == 204 and deleted_set.status_code == 204
    finally:
        main.app.dependency_overrides.clear()


def test_research_assets_allow_viewers_to_read_but_not_write_or_cross_workspace(tmp_path):
    store = _setup(tmp_path)
    alice, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
    bob, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
    store.add_workspace_member(workspace.id, bob.id, "viewer")
    try:
        with TestClient(main.app) as client:
            question = client.post("/api/research/questions", headers=_headers("alice"), json={"question": "Can viewers read this?"})
            source_set = client.post("/api/source-sets", headers=_headers("alice"), json={"name": "Shared"})
            question_id, source_set_id = question.json()["id"], source_set.json()["id"]
            reader = client.get("/api/research/questions", headers=_headers("bob", workspace.id))
            set_reader = client.get(f"/api/source-sets/{source_set_id}", headers=_headers("bob", workspace.id))
            question_write = client.patch(f"/api/research/questions/{question_id}", headers=_headers("bob", workspace.id), json={"title": "No"})
            set_write = client.delete(f"/api/source-sets/{source_set_id}", headers=_headers("bob", workspace.id))
            cross_workspace = client.get(f"/api/research/questions/{question_id}", headers=_headers("bob"))

        assert reader.status_code == 200 and reader.json()[0]["id"] == question_id
        assert set_reader.status_code == 200 and set_reader.json()["id"] == source_set_id
        assert question_write.status_code == 403 and set_write.status_code == 403
        assert cross_workspace.status_code == 404
    finally:
        main.app.dependency_overrides.clear()
