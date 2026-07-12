from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import extraction, main
from app.celery_app import celery_app, ingest_task
from app.database import Base, DocumentElementRecord
from app.extraction import DocumentExtractor, ExtractionConfig
from app.storage import LocalOriginalStorage
from app.store import PaperStore


class FakeOCR:
    def __init__(self, text="OCR extracted text"):
        self.text, self.calls = text, 0

    def extract_page(self, pdf_bytes, page_index, languages, timeout):
        self.calls += 1
        assert languages == "jpn+eng" and timeout > 0
        return self.text


class FailingOCR:
    def extract_page(self, *args, **kwargs):
        raise RuntimeError("tesseract unavailable")


class FakeTables:
    def extract(self, pdf_bytes, max_pages):
        return {1: [[['Column A', 'Column B'], ['one', 'two']]]}


class FakePage:
    mediabox = SimpleNamespace(width=600, height=800)
    images = [SimpleNamespace(name="figure.png", data=b"png-bytes")]

    def __init__(self, text): self._text = text
    def extract_text(self): return self._text


def test_feature_off_skips_ocr_and_extracts_table_figure_caption(monkeypatch, tmp_path):
    monkeypatch.setattr(extraction, "PdfReader", lambda _: SimpleNamespace(pages=[FakePage("Figure 1 Overview")]))
    ocr = FakeOCR()
    storage = LocalOriginalStorage(tmp_path / "assets")
    result = DocumentExtractor(ExtractionConfig(enable_ocr=False), ocr=ocr, tables=FakeTables()).extract(b"fake", "paper.pdf", "paper-id", storage)
    assert ocr.calls == 0
    assert result.pages[0].text_source == "native"
    kinds = {element.kind for element in result.elements}
    assert {"text", "table", "figure", "caption"} <= kinds
    table = next(element for element in result.elements if element.kind == "table")
    assert table.structured_data["rows"][1] == ["one", "two"]
    figure = next(element for element in result.elements if element.kind == "figure")
    assert storage.path_for(figure.asset_key).read_bytes() == b"png-bytes"


def test_low_density_page_uses_ocr_only_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(extraction, "PdfReader", lambda _: SimpleNamespace(pages=[FakePage("")]))
    ocr = FakeOCR("日本語 OCR result")
    result = DocumentExtractor(ExtractionConfig(enable_ocr=True, ocr_density_threshold=100), ocr=ocr, tables=FakeTables()).extract(b"fake", "paper.pdf", "paper-id", LocalOriginalStorage(tmp_path / "assets"))
    assert ocr.calls == 1
    assert result.pages[0].text_source == "ocr"
    assert "OCR result" in result.pages[0].text


def test_ocr_dependency_failure_has_explicit_native_or_fail_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(extraction, "PdfReader", lambda _: SimpleNamespace(pages=[FakePage("small")]))
    storage = LocalOriginalStorage(tmp_path / "assets")
    fallback = DocumentExtractor(ExtractionConfig(enable_ocr=True, ocr_density_threshold=100, ocr_failure_policy="native"), ocr=FailingOCR(), tables=FakeTables()).extract(b"fake", "paper.pdf", "paper-id", storage)
    assert fallback.pages[0].text == "small" and fallback.pages[0].text_source == "native"
    import pytest
    with pytest.raises(RuntimeError, match="tesseract unavailable"):
        DocumentExtractor(ExtractionConfig(enable_ocr=True, ocr_density_threshold=100, ocr_failure_policy="fail"), ocr=FailingOCR(), tables=FakeTables()).extract(b"fake", "paper.pdf", "other-paper", storage)


def test_inline_job_page_assets_and_job_idor(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: LocalOriginalStorage(tmp_path / "originals")
    try:
        with TestClient(main.app) as client:
            result = client.post("/api/papers/upload", headers={"X-Dev-User": "alice"}, files={"files": ("paper.txt", b"native evidence text", "text/plain")}).json()[0]
            paper_id, job_id = result["paper"]["id"], result["job"]["id"]
            job = client.get(f"/api/jobs/{job_id}", headers={"X-Dev-User": "alice"})
            page = client.get(f"/api/papers/{paper_id}/pages/1", headers={"X-Dev-User": "alice"})
            assets = client.get(f"/api/papers/{paper_id}/assets", headers={"X-Dev-User": "alice"})
            idor = client.get(f"/api/jobs/{job_id}", headers={"X-Dev-User": "bob"})
        assert job.json()["status"] == "succeeded" and job.json()["progress"] == 100
        assert page.json()["text_source"] == "native" and page.json()["elements"][0]["kind"] == "text"
        assert assets.json()[0]["paper_id"] == paper_id
        assert idor.status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_celery_task_contract_uses_identifier_arguments_only():
    assert ingest_task.name == "paperpilot.ingest"
    assert list(ingest_task.run.__annotations__)[:2] == ["paper_id", "job_id"]
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.beat_schedule["reap-stale-ingestion-jobs"]["task"] == "paperpilot.reap_ingestion_jobs"


def test_figure_asset_file_is_workspace_scoped_and_hides_storage_key(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'asset-api.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = PaperStore(session_factory=factory)
    originals = LocalOriginalStorage(tmp_path / "originals")
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: originals
    try:
        with TestClient(main.app) as client:
            uploaded = client.post("/api/papers/upload", headers={"X-Dev-User": "alice"}, files={"files": ("paper.txt", b"native evidence", "text/plain")}).json()[0]
            paper_id = uploaded["paper"]["id"]
            element_id, asset_key = "figure-element", f"papers/{paper_id}/assets/figure.png"
            originals.put(asset_key, b"PNG image bytes")
            with factory.begin() as session:
                session.add(DocumentElementRecord(id=element_id, paper_id=paper_id, page=1, kind="figure", bbox=None, text="", structured_data=None, asset_key=asset_key))
            allowed = client.get(f"/api/papers/{paper_id}/assets/{element_id}/file", headers={"X-Dev-User": "alice"})
            denied = client.get(f"/api/papers/{paper_id}/assets/{element_id}/file", headers={"X-Dev-User": "bob"})
        assert allowed.status_code == 200 and allowed.content == b"PNG image bytes"
        assert allowed.headers["content-type"].startswith("image/png")
        assert asset_key not in allowed.headers["content-disposition"]
        assert denied.status_code == 404
    finally:
        main.app.dependency_overrides.clear()
