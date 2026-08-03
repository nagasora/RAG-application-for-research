import hashlib
import sys
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import extraction
from app.database import Base
from app.extraction import DocumentExtractor, ExtractionConfig
from app.ingestion import process_ingestion_job
from app.models import Chunk, Paper, Principal
from app.rag import chunk_pages
from app.storage import LocalOriginalStorage
from app.store import PaperStore


class NulTextPage:
    mediabox = SimpleNamespace(width=600, height=800)
    images = []

    def extract_text(self):
        return "Figure 1\x00 Evidence from a PDF"


class NulTableAdapter:
    def extract(self, _pdf_bytes, _max_pages):
        return {1: [[["Measure\x00ment", "Value"], ["mass", "2\x000"]]]}


class EmptyCaptionLocator:
    def locate(self, _pdf_bytes, _max_pages):
        return {}


class FakeChunkingConfig:
    def __init__(self, **_kwargs):
        pass


def fake_dynamic_chunk_pages(pages, paper_id, _config):
    return [Chunk(paper_id=paper_id, page=page, text=text) for page, text in pages]


def install_fake_chunker(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "app.agentic_rag",
        SimpleNamespace(
            DynamicChunkingConfig=FakeChunkingConfig,
            dynamic_chunk_pages=fake_dynamic_chunk_pages,
        ),
    )


def test_pdf_extraction_and_chunking_strip_postgres_nul(monkeypatch, tmp_path):
    monkeypatch.setattr(
        extraction,
        "PdfReader",
        lambda _: SimpleNamespace(
            pages=[NulTextPage()], metadata=SimpleNamespace(title="\x00 NUL title"),
        ),
    )
    result = DocumentExtractor(
        ExtractionConfig(), tables=NulTableAdapter(), caption_locator=EmptyCaptionLocator(),
    ).extract(b"fake", "paper.pdf", "paper-id", LocalOriginalStorage(tmp_path / "assets"))

    install_fake_chunker(monkeypatch)
    chunks = chunk_pages([(page.page, page.text) for page in result.pages], "paper-id")
    direct_chunks = chunk_pages([(1, "external abstract\x00 text")], "paper-id")
    table = next(element for element in result.elements if element.kind == "table")
    caption = next(element for element in result.elements if element.kind == "caption")
    persisted_text = [
        result.title,
        *(page.text for page in result.pages),
        *(element.text for element in result.elements),
        *(chunk.text for chunk in chunks),
        *(chunk.text for chunk in direct_chunks),
        *(cell for row in table.structured_data["rows"] for cell in row if cell is not None),
    ]

    assert all("\x00" not in value for value in persisted_text)
    assert result.title == "NUL title"
    assert table.structured_data["rows"][1][1] == "20"
    assert caption.structured_data["label"] == "Figure 1"


def test_pdf_ingestion_persists_nul_free_text_and_provenance(monkeypatch, tmp_path):
    monkeypatch.setattr(
        extraction,
        "PdfReader",
        lambda _: SimpleNamespace(
            pages=[NulTextPage()], metadata=SimpleNamespace(title="\x00 NUL title"),
        ),
    )
    install_fake_chunker(monkeypatch)
    engine = create_engine(f"sqlite:///{tmp_path / 'ingestion.db'}")
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    user, workspace = store.ensure_user(Principal(issuer="test", subject="nul-user"))
    paper = Paper(
        user_id="nul-user", workspace_id=workspace.id, created_by=user.id,
        title="Original title", status="processing", storage_key="papers/nul.pdf",
        content_hash=hashlib.sha256(b"fake").hexdigest(),
    )
    store.begin_processing(paper)
    job = store.create_ingestion_job(workspace.id, paper.id)
    storage = LocalOriginalStorage(tmp_path / "originals")
    storage.put(paper.storage_key, b"fake")

    process_ingestion_job(
        store, storage, job.id, paper.id,
        extractor=DocumentExtractor(
            ExtractionConfig(), tables=NulTableAdapter(), caption_locator=EmptyCaptionLocator(),
        ),
    )

    saved = store.get(paper.id)
    page = store.get_page_extraction(workspace.id, paper.id, 1)
    elements = store.list_document_elements(workspace.id, paper.id)
    source_version_id = store.paper_source_version_id(workspace.id, paper.id)
    spans = store.list_source_spans(workspace.id, source_version_id)
    persisted_text = [
        saved.title, saved.abstract,
        *(chunk.text for chunk in saved.chunks),
        page.text,
        *(element.text for element in elements),
        *(cell for element in elements if element.structured_data for row in element.structured_data.get("rows", []) for cell in row if cell is not None),
        *(span.text for span in spans),
    ]

    assert store.get_ingestion_job(workspace.id, job.id).status == "succeeded"
    assert saved.status == "ready"
    assert spans
    assert all("\x00" not in value for value in persisted_text)
