from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base
from app.models import Principal
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def setup_app(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'research.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: LocalOriginalStorage(tmp_path / "originals")
    return store


def headers(user, workspace_id=None):
    value = {"X-Dev-User": user}
    if workspace_id: value["X-Workspace-ID"] = workspace_id
    return value


def test_assets_are_workspace_scoped_and_viewer_is_read_only(tmp_path):
    store = setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            uploaded = client.post("/api/papers/upload", headers=headers("alice"), files={"files": ("study.txt", b"retrieval evidence", "text/plain")}).json()[0]
            paper_id = uploaded["paper"]["id"]
            tag = client.post("/api/tags", headers=headers("alice"), json={"name": "Important", "color": "#ff0000"})
            tag_id = tag.json()["id"]
            attached = client.put(f"/api/papers/{paper_id}/tags", headers=headers("alice"), json={"tag_ids": [tag_id]})
            note = client.post("/api/notes", headers=headers("alice"), json={"paper_id": paper_id, "title": "Finding", "content": "Reproduce this"})

            alice, workspace = store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))
            bob, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
            store.add_workspace_member(workspace.id, bob.id, "viewer")
            viewer_tags = client.get("/api/tags", headers=headers("bob", workspace.id))
            viewer_notes = client.get("/api/notes", headers=headers("bob", workspace.id))
            forbidden = client.post("/api/tags", headers=headers("bob", workspace.id), json={"name": "Nope", "color": "red"})
            idor = client.patch(f"/api/notes/{note.json()['id']}", headers=headers("bob"), json={"title": "stolen"})

        assert tag.status_code == 201 and attached.status_code == 200 and note.status_code == 201
        assert viewer_tags.status_code == 200 and viewer_tags.json()[0]["id"] == tag_id
        assert viewer_notes.status_code == 200 and viewer_notes.json()[0]["title"] == "Finding"
        assert forbidden.status_code == 403
        assert idor.status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_search_history_saved_comparison_and_exports(tmp_path):
    setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            first = client.post("/api/papers/upload", headers=headers("alice"), files={"files": ("=DANGEROUS.txt", b"document retrieval evidence", "text/plain")}).json()[0]["paper"]
            second = client.post("/api/papers/upload", headers=headers("alice"), files={"files": ("Study.txt", b"document retrieval method", "text/plain")}).json()[0]["paper"]
            search = client.post("/api/search", headers=headers("alice"), json={"query": "document retrieval", "paper_ids": [first["id"]]})
            history = client.get("/api/search/history", headers=headers("alice"))
            saved = client.post("/api/comparisons", headers=headers("alice"), json={"name": "Methods", "paper_ids": [first["id"], second["id"]]})
            listed = client.get("/api/comparisons", headers=headers("alice"))
            csv_export = client.get("/api/exports/papers?format=csv", headers=headers("alice"))
            bib = client.get("/api/exports/papers?format=bibtex", headers=headers("alice"))
            ris = client.get("/api/exports/papers?format=ris", headers=headers("alice"))
            deleted = client.delete(f"/api/comparisons/{saved.json()['id']}", headers=headers("alice"))

        assert search.status_code == 200
        assert history.status_code == 200 and history.json()[0]["query"] == "document retrieval"
        assert "answer" not in history.json()[0]["result_summary"]
        assert saved.status_code == 201 and listed.json()[0]["name"] == "Methods" and deleted.status_code == 204
        assert csv_export.status_code == 200 and "'=DANGEROUS" in csv_export.text
        assert "filename=\"paperpilot-export.csv\"" in csv_export.headers["content-disposition"]
        assert "@article{" in bib.text and "TY  - JOUR" in ris.text
    finally:
        main.app.dependency_overrides.clear()
