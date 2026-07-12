from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base
from app.models import Principal
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def make_store(tmp_path) -> PaperStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))


def dev(user: str) -> dict[str, str]:
    return {"X-Dev-User": user}


def personal_workspace(store: PaperStore, subject: str):
    return store.ensure_user(Principal(issuer="paperpilot-dev", subject=subject))[1]


def test_upload_returns_one_result_per_file_and_persists_states(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    originals = LocalOriginalStorage(tmp_path / "originals")
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: originals
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 16)
    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/api/papers/upload",
                data={"user_id": "u"},
                headers=dev("u"),
                files=[
                    ("files", ("good.txt", b"grounded study", "text/plain")),
                    ("files", ("large.txt", b"x" * 17, "text/plain")),
                    ("files", ("empty.txt", b"", "text/plain")),
                ],
            )
        assert response.status_code == 200
        results = response.json()
        assert [item["success"] for item in results] == [True, False, False]
        assert [item["status"] for item in results] == ["ready", "rejected", "rejected"]
        assert results[0]["paper"]["status"] == "ready"
        assert results[1]["paper"] is None
        assert results[2]["paper"] is None
        assert [paper.status for paper in store.list(personal_workspace(store, "u").id)] == ["ready"]
    finally:
        main.app.dependency_overrides.clear()


def test_parse_failure_is_retained_as_failed_and_duplicate_is_rejected(tmp_path):
    store = make_store(tmp_path)
    originals = LocalOriginalStorage(tmp_path / "originals")
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: originals
    try:
        with TestClient(main.app) as client:
            first = client.post(
                "/api/papers/upload",
                data={"user_id": "u"},
                headers=dev("u"),
                files={"files": ("broken.pdf", b"not-a-pdf", "application/pdf")},
            )
            second = client.post(
                "/api/papers/upload",
                data={"user_id": "u"},
                headers=dev("u"),
                files={"files": ("same.pdf", b"not-a-pdf", "application/pdf")},
            )
        assert first.status_code == 200
        assert first.json()[0]["paper"]["status"] == "failed"
        assert second.json()[0]["duplicate"] is True
        assert second.json()[0]["success"] is True
        assert second.json()[0]["status"] == "duplicate"
        workspace = personal_workspace(store, "u")
        assert len(store.list(workspace.id)) == 1
        failed = store.list(workspace.id)[0]
        assert originals.path_for(failed.storage_key).read_bytes() == b"not-a-pdf"
    finally:
        main.app.dependency_overrides.clear()


def test_detail_evidence_file_range_and_delete_are_owner_scoped(tmp_path):
    store = make_store(tmp_path)
    originals = LocalOriginalStorage(tmp_path / "originals")
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: originals
    try:
        with TestClient(main.app) as client:
            uploaded = client.post(
                "/api/papers/upload",
                data={"user_id": "owner"},
                headers=dev("owner"),
                files={"files": ("study.txt", b"grounded study result", "text/plain")},
            ).json()[0]
            paper_id = uploaded["paper"]["id"]
            detail = client.get(f"/api/papers/{paper_id}?user_id=ignored", headers=dev("owner"))
            page = client.get(f"/api/papers/{paper_id}/pages/1", headers=dev("owner"))
            chunk_id = page.json()["chunks"][0]["id"]
            chunk = client.get(f"/api/papers/{paper_id}/chunks/{chunk_id}", headers=dev("owner"))
            forbidden = client.get(f"/api/papers/{paper_id}/file?user_id=owner", headers=dev("other"))
            ranged = client.get(
                f"/api/papers/{paper_id}/file",
                headers={**dev("owner"), "Range": "bytes=0-7"},
            )
            deleted = client.delete(f"/api/papers/{paper_id}", headers=dev("owner"))

        assert detail.status_code == 200
        assert detail.json()["mime_type"].startswith("text/plain")
        assert detail.json()["byte_size"] == len(b"grounded study result")
        assert chunk.status_code == 200
        assert chunk.json()["id"] == chunk_id
        assert forbidden.status_code == 404
        assert ranged.status_code == 206
        assert ranged.content == b"grounded"
        assert deleted.status_code == 204
        assert not (tmp_path / "originals" / detail.json()["storage_key"]).exists()
    finally:
        main.app.dependency_overrides.clear()


def test_original_storage_failure_clears_file_metadata_and_marks_failed(tmp_path):
    class FailingStorage:
        def put(self, key, content):
            raise OSError("disk full")

        def path_for(self, key):
            raise FileNotFoundError(key)

        def delete(self, key):
            return None

    store = make_store(tmp_path)
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: FailingStorage()
    try:
        with TestClient(main.app) as client:
            result = client.post(
                "/api/papers/upload",
                data={"user_id": "u"},
                headers=dev("u"),
                files={"files": ("study.txt", b"content", "text/plain")},
            ).json()[0]
        saved = store.list(personal_workspace(store, "u").id)[0]
        assert result["status"] == "failed"
        assert saved.status == "failed"
        assert saved.storage_key is None
        assert saved.mime_type is None
        assert saved.byte_size is None
    finally:
        main.app.dependency_overrides.clear()
