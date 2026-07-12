from __future__ import annotations

import io
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from pypdf import PdfReader

from .models import DocumentElement, PaperPage
from .rag import chunk_pages, normalize
from .storage import OriginalStorage


class ExtractionError(Exception):
    pass


@dataclass(frozen=True)
class ExtractionConfig:
    enable_ocr: bool = False
    ocr_languages: str = "jpn+eng"
    ocr_density_threshold: float = 100.0
    ocr_timeout_seconds: int = 20
    ocr_failure_policy: str = "native"
    max_pages: int = 300
    max_seconds: int = 300
    max_cpu_seconds: int = 240
    max_assets: int = 100
    max_asset_bytes: int = 20 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "ExtractionConfig":
        return cls(
            enable_ocr=os.getenv("ENABLE_OCR", "false").lower() in {"1", "true", "yes"},
            ocr_languages=os.getenv("OCR_LANGUAGES", "jpn+eng"),
            ocr_density_threshold=float(os.getenv("OCR_DENSITY_THRESHOLD", "100")),
            ocr_timeout_seconds=int(os.getenv("OCR_TIMEOUT_SECONDS", "20")),
            ocr_failure_policy=os.getenv("OCR_FAILURE_POLICY", "native"),
            max_pages=int(os.getenv("INGESTION_MAX_PAGES", os.getenv("MAX_PDF_PAGES", "300"))),
            max_seconds=int(os.getenv("INGESTION_MAX_SECONDS", "300")),
            max_cpu_seconds=int(os.getenv("INGESTION_MAX_CPU_SECONDS", "240")),
            max_assets=int(os.getenv("INGESTION_MAX_ASSETS", "100")),
            max_asset_bytes=int(os.getenv("INGESTION_MAX_ASSET_BYTES", str(20 * 1024 * 1024))),
        )


class OCRAdapter(Protocol):
    def extract_page(self, pdf_bytes: bytes, page_index: int, languages: str, timeout: int) -> str: ...


class TesseractOCRAdapter:
    def extract_page(self, pdf_bytes: bytes, page_index: int, languages: str, timeout: int) -> str:
        try:
            import pypdfium2 as pdfium
            import pytesseract
        except ImportError as exc:
            raise ExtractionError("OCR dependencies are unavailable (pypdfium2/pytesseract)") from exc
        try:
            document = pdfium.PdfDocument(pdf_bytes)
            image = document[page_index].render(scale=2).to_pil()
            return pytesseract.image_to_string(image, lang=languages, timeout=timeout)
        except RuntimeError as exc:
            raise ExtractionError(f"OCR timed out or failed: {exc}") from exc
        except Exception as exc:
            raise ExtractionError(f"OCR CLI or language data unavailable: {exc}") from exc


class TableAdapter(Protocol):
    def extract(self, pdf_bytes: bytes, max_pages: int) -> dict[int, list[list[list[str | None]]]]: ...


class PdfPlumberTableAdapter:
    def extract(self, pdf_bytes: bytes, max_pages: int) -> dict[int, list[list[list[str | None]]]]:
        try:
            import pdfplumber
        except ImportError:
            return {}
        result: dict[int, list[list[list[str | None]]]] = {}
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
            for index, page in enumerate(document.pages[:max_pages], 1):
                tables = page.extract_tables() or []
                if tables:
                    result[index] = tables
        return result


def _table_markdown(rows: list[list[str | None]]) -> str:
    if not rows:
        return ""
    clean = [[(cell or "").replace("|", "\\|").replace("\n", " ") for cell in row] for row in rows]
    width = max(len(row) for row in clean)
    clean = [row + [""] * (width - len(row)) for row in clean]
    return "| " + " | ".join(clean[0]) + " |\n| " + " | ".join(["---"] * width) + " |\n" + "\n".join("| " + " | ".join(row) + " |" for row in clean[1:])


@dataclass
class ExtractionResult:
    pages: list[PaperPage]
    elements: list[DocumentElement]
    title: str | None = None


class DocumentExtractor:
    """Bounded extraction pipeline; heavier document engines can implement the same boundary later."""

    def __init__(self, config: ExtractionConfig, ocr: OCRAdapter | None = None, tables: TableAdapter | None = None):
        self.config = config
        self.ocr = ocr or TesseractOCRAdapter()
        self.tables = tables or PdfPlumberTableAdapter()
        self.created_asset_keys: list[str] = []

    def extract(self, content: bytes, filename: str, paper_id: str, storage: OriginalStorage) -> ExtractionResult:
        self.created_asset_keys = []
        if filename.lower().endswith(".pdf"):
            return self._extract_pdf(content, paper_id, storage)
        text = normalize(content.decode("utf-8", errors="replace"))
        if not text:
            raise ExtractionError("本文を抽出できませんでした")
        element = DocumentElement(id=str(uuid4()), paper_id=paper_id, page=1, kind="text", text=text)
        return ExtractionResult([PaperPage(paper_id=paper_id, page=1, chunks=[], text=text, text_source="native", quality=min(1.0, len(text) / 500))], [element])

    def _extract_pdf(self, content: bytes, paper_id: str, storage: OriginalStorage) -> ExtractionResult:
        started = time.monotonic()
        cpu_started = time.process_time()
        reader = PdfReader(io.BytesIO(content))
        if len(reader.pages) > self.config.max_pages:
            raise ExtractionError(f"PDF page limit exceeded ({self.config.max_pages})")
        try:
            tables_by_page = self.tables.extract(content, self.config.max_pages)
        except Exception:
            tables_by_page = {}
        pages: list[PaperPage] = []
        elements: list[DocumentElement] = []
        asset_count = 0
        for index, page in enumerate(reader.pages):
            if time.monotonic() - started > self.config.max_seconds or time.process_time() - cpu_started > self.config.max_cpu_seconds:
                raise ExtractionError("ingestion time limit exceeded")
            raw_text = page.extract_text() or ""
            native = normalize(raw_text)
            width, height = float(page.mediabox.width or 1), float(page.mediabox.height or 1)
            density = len(native) * 1_000_000 / max(1.0, width * height)
            text, source = native, ("native" if native else "none")
            if self.config.enable_ocr and density < self.config.ocr_density_threshold:
                try:
                    ocr_text = normalize(self.ocr.extract_page(content, index, self.config.ocr_languages, self.config.ocr_timeout_seconds))
                    if ocr_text:
                        text, source = ocr_text, "ocr"
                except Exception:
                    if self.config.ocr_failure_policy == "fail":
                        raise
            page_number = index + 1
            effective_density = len(text) * 1_000_000 / max(1.0, width * height)
            pages.append(PaperPage(paper_id=paper_id, page=page_number, chunks=[], text=text, text_source=source, quality=min(1.0, effective_density / max(1.0, self.config.ocr_density_threshold))))
            if text:
                elements.append(DocumentElement(id=str(uuid4()), paper_id=paper_id, page=page_number, kind="text", text=text))
            for table in tables_by_page.get(page_number, []):
                if asset_count >= self.config.max_assets:
                    break
                elements.append(DocumentElement(id=str(uuid4()), paper_id=paper_id, page=page_number, kind="table", text=_table_markdown(table), structured_data={"rows": table}))
                asset_count += 1
            try:
                page_images = list(getattr(page, "images", []))
            except Exception:
                page_images = []
            for image in page_images:
                if asset_count >= self.config.max_assets:
                    break
                data = getattr(image, "data", None)
                if not data or len(data) > self.config.max_asset_bytes:
                    continue
                element_id = str(uuid4())
                extension = re.sub(r"[^a-z0-9]", "", str(getattr(image, "name", "png")).split(".")[-1].lower()) or "png"
                key = f"assets/papers/{paper_id}/{element_id}.{extension[:5]}"
                storage.put(key, data); self.created_asset_keys.append(key); asset_count += 1
                elements.append(DocumentElement(id=element_id, paper_id=paper_id, page=page_number, kind="figure", asset_key=key, structured_data={"byte_size": len(data)}))
                caption_match = re.search(r"(?:^|[\r\n])\s*((?:figure|fig\.|図)\s*\d+[^\r\n]*)", raw_text, re.I)
                caption = caption_match.group(1).strip() if caption_match else ""
                if caption:
                    elements.append(DocumentElement(id=str(uuid4()), paper_id=paper_id, page=page_number, kind="caption", text=caption))
        metadata = getattr(reader, "metadata", None)
        title = metadata.title.strip() if metadata and metadata.title else None
        return ExtractionResult(pages, elements, title)
