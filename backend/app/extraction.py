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
from .rag import chunk_pages, normalize, strip_nul
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


@dataclass(frozen=True)
class CaptionLocation:
    target_kind: str
    label: str
    text: str
    bbox: list[float]


class CaptionLocatorAdapter(Protocol):
    def locate(self, pdf_bytes: bytes, max_pages: int) -> dict[int, list[CaptionLocation]]: ...


class PdfPlumberCaptionLocatorAdapter:
    """Locate labelled caption lines without guessing a figure's boundary."""

    def locate(self, pdf_bytes: bytes, max_pages: int) -> dict[int, list[CaptionLocation]]:
        try:
            import pdfplumber
        except ImportError:
            return {}
        result: dict[int, list[CaptionLocation]] = {}
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
            for index, page in enumerate(document.pages[:max_pages], 1):
                try:
                    words = page.extract_words(use_text_flow=True) or []
                except Exception:
                    continue
                lines: dict[float, list[dict]] = {}
                for word in words:
                    try:
                        lines.setdefault(round(float(word["top"]), 1), []).append(word)
                    except (KeyError, TypeError, ValueError):
                        continue
                locations: list[CaptionLocation] = []
                for line in lines.values():
                    ordered = sorted(line, key=lambda item: float(item["x0"]))
                    text = strip_nul(" ".join(str(item.get("text", "")) for item in ordered)).strip()
                    candidates = _caption_candidates(text)
                    if not candidates:
                        continue
                    try:
                        bbox = [
                            min(float(item["x0"]) for item in ordered), min(float(item["top"]) for item in ordered),
                            max(float(item["x1"]) for item in ordered), max(float(item["bottom"]) for item in ordered),
                        ]
                    except (KeyError, TypeError, ValueError):
                        continue
                    target_kind, label, caption = candidates[0]
                    locations.append(CaptionLocation(target_kind, label, caption, bbox))
                if locations:
                    result[index] = locations
        return result


class PageCropRenderer(Protocol):
    def render_crop(self, pdf_bytes: bytes, page_index: int, page_width: float, page_height: float, bbox: list[float]) -> bytes | None: ...


class PdfiumPageCropRenderer:
    """Render a conservative page crop only after a caption is unambiguous."""

    def render_crop(self, pdf_bytes: bytes, page_index: int, page_width: float, page_height: float, bbox: list[float]) -> bytes | None:
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(pdf_bytes)
            image = document[page_index].render(scale=2).to_pil()
            x0, y0, x1, y1 = bbox
            left = max(0, min(image.width, round(x0 / page_width * image.width)))
            top = max(0, min(image.height, round(y0 / page_height * image.height)))
            right = max(left + 1, min(image.width, round(x1 / page_width * image.width)))
            bottom = max(top + 1, min(image.height, round(y1 / page_height * image.height)))
            if right <= left or bottom <= top:
                return None
            output = io.BytesIO()
            image.crop((left, top, right, bottom)).save(output, format="PNG", optimize=True)
            return output.getvalue()
        except Exception:
            return None


def _table_markdown(rows: list[list[str | None]]) -> str:
    if not rows:
        return ""
    clean = [[strip_nul(cell or "").replace("|", "\\|").replace("\n", " ") for cell in row] for row in rows]
    width = max(len(row) for row in clean)
    clean = [row + [""] * (width - len(row)) for row in clean]
    return "| " + " | ".join(clean[0]) + " |\n| " + " | ".join(["---"] * width) + " |\n" + "\n".join("| " + " | ".join(row) + " |" for row in clean[1:])


_CAPTION_LINE_RE = re.compile(
    r"^\s*((?:figure|fig\.?|table|図|表)\s*[0-9０-９]+[A-Za-zＡ-Ｚ]?(?:\s*[:.：]?\s+.*)?)$",
    re.IGNORECASE,
)


def _caption_candidates(raw_text: str) -> list[tuple[str, str, str]]:
    """Keep only labelled captions; unlabeled nearby prose is never a caption."""

    candidates: list[tuple[str, str, str]] = []
    for line in strip_nul(raw_text).splitlines():
        match = _CAPTION_LINE_RE.match(line)
        if not match:
            continue
        caption = match.group(1).strip()
        label = re.match(r"(?:figure|fig\.?|table|図|表)\s*[0-9０-９]+[A-Za-zＡ-Ｚ]?", caption, re.I)
        if not label:
            continue
        target_kind = "figure" if re.match(r"(?:figure|fig\.?|図)", label.group(0), re.I) else "table"
        candidates.append((target_kind, label.group(0), caption))
    return candidates


def _attach_page_captions(page_elements: list[DocumentElement], raw_text: str, paper_id: str, page: int, locations: list[CaptionLocation] | None = None) -> list[DocumentElement]:
    """Record labelled captions without guessing their visual target.

    A page can contain a single embedded image that is merely a publisher
    logo.  Cardinality alone is therefore not evidence of a caption relation.
    A later geometry-aware extractor may add ``related_element_id`` together
    with high confidence; page-derived crops remain explicitly unresolved.
    """

    captions: list[DocumentElement] = []
    for target_kind, label, caption in _caption_candidates(raw_text):
        metadata = {
            "target_kind": target_kind,
            "label": label,
            "caption_relation": "unresolved",
            "caption_relation_confidence": "medium",
        }
        location = next((item for item in locations or [] if item.target_kind == target_kind and item.label.casefold() == label.casefold()), None)
        bbox = location.bbox if location is not None else None
        captions.append(DocumentElement(id=str(uuid4()), paper_id=paper_id, page=page, kind="caption", bbox=bbox, text=caption, structured_data=metadata))
    return captions


@dataclass
class ExtractionResult:
    pages: list[PaperPage]
    elements: list[DocumentElement]
    title: str | None = None


class DocumentExtractor:
    """Bounded extraction pipeline; heavier document engines can implement the same boundary later."""

    def __init__(self, config: ExtractionConfig, ocr: OCRAdapter | None = None, tables: TableAdapter | None = None, caption_locator: CaptionLocatorAdapter | None = None, crop_renderer: PageCropRenderer | None = None):
        self.config = config
        self.ocr = ocr or TesseractOCRAdapter()
        self.tables = tables or PdfPlumberTableAdapter()
        self.caption_locator = caption_locator or PdfPlumberCaptionLocatorAdapter()
        self.crop_renderer = crop_renderer or PdfiumPageCropRenderer()
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
        try:
            captions_by_page = self.caption_locator.locate(content, self.config.max_pages)
        except Exception:
            captions_by_page = {}
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
            page_elements: list[DocumentElement] = []
            effective_density = len(text) * 1_000_000 / max(1.0, width * height)
            pages.append(PaperPage(paper_id=paper_id, page=page_number, chunks=[], text=text, text_source=source, quality=min(1.0, effective_density / max(1.0, self.config.ocr_density_threshold))))
            if text:
                page_elements.append(DocumentElement(id=str(uuid4()), paper_id=paper_id, page=page_number, kind="text", text=text))
            for table in tables_by_page.get(page_number, []):
                if asset_count >= self.config.max_assets:
                    break
                sanitized_table = [[strip_nul(cell) if cell is not None else None for cell in row] for row in table]
                page_elements.append(DocumentElement(id=str(uuid4()), paper_id=paper_id, page=page_number, kind="table", text=_table_markdown(sanitized_table), structured_data={"rows": sanitized_table}))
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
                page_elements.append(DocumentElement(id=element_id, paper_id=paper_id, page=page_number, kind="figure", asset_key=key, structured_data={"byte_size": len(data)}))
            page_elements.extend(_attach_page_captions(page_elements, text, paper_id, page_number, captions_by_page.get(page_number)))
            crop_elements, crop_count = self._caption_crops(
                content, index, width, height, page_elements, paper_id, storage, self.config.max_assets - asset_count,
            )
            page_elements.extend(crop_elements)
            asset_count += crop_count
            elements.extend(page_elements)
        metadata = getattr(reader, "metadata", None)
        title = normalize(metadata.title) if metadata and metadata.title else None
        return ExtractionResult(pages, elements, title)

    def _caption_crops(self, content: bytes, page_index: int, width: float, height: float, page_elements: list[DocumentElement], paper_id: str, storage: OriginalStorage, remaining_assets: int) -> tuple[list[DocumentElement], int]:
        """Create a page-derived PNG only for an unambiguous caption relation."""

        if remaining_assets <= 0:
            return [], 0
        elements_by_id = {element.id: element for element in page_elements}
        crops: list[DocumentElement] = []
        for caption in (element for element in page_elements if element.kind == "caption" and element.bbox and isinstance(element.structured_data, dict)):
            if len(crops) >= remaining_assets:
                break
            metadata = caption.structured_data
            target_id = metadata.get("related_element_id")
            target_kind = metadata.get("target_kind")
            target = elements_by_id.get(str(target_id))
            if target_kind not in {"figure", "table"}:
                continue
            targets_of_kind = [item for item in page_elements if item.kind == target_kind]
            captions_of_kind = [
                item for item in page_elements
                if item.kind == "caption" and isinstance(item.structured_data, dict)
                and item.structured_data.get("target_kind") == target_kind
            ]
            vector_only = target is None and target_kind == "figure" and not targets_of_kind and len(captions_of_kind) == 1
            sole_unverified_target = target is None and len(targets_of_kind) == 1 and len(captions_of_kind) == 1
            if sole_unverified_target:
                target = targets_of_kind[0]
            if target is not None and target.kind != target_kind:
                continue
            if target is None and not vector_only:
                # Existing visuals without an explicit target, or multiple
                # caption candidates, are ambiguous rather than page crops.
                continue
            x0, y0, x1, y1 = caption.bbox
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                continue
            # Figures conventionally precede their caption; tables commonly
            # follow it.  The crop is labelled page-derived, not as a detected
            # graph boundary, so clients can show its confidence honestly.
            padding = min(18.0, max(4.0, height * 0.015))
            if target_kind == "figure":
                crop_bbox = [0.0, max(0.0, y0 - height * 0.60), width, max(0.0, y0 - padding)]
            else:
                crop_bbox = [0.0, min(height, y1 + padding), width, min(height, y1 + height * 0.60)]
            if crop_bbox[3] - crop_bbox[1] < max(24.0, height * 0.06):
                continue
            data = self.crop_renderer.render_crop(content, page_index, width, height, crop_bbox)
            if not data or len(data) > self.config.max_asset_bytes:
                continue
            element_id = str(uuid4())
            key = f"assets/papers/{paper_id}/{element_id}.png"
            storage.put(key, data)
            self.created_asset_keys.append(key)
            crop = DocumentElement(
                id=element_id, paper_id=paper_id, page=caption.page, kind=target_kind, bbox=crop_bbox, asset_key=key,
                structured_data={"derived_from_page": True, "caption_element_id": caption.id, "source_element_id": target.id if target else None, "crop_confidence": "medium"},
            )
            if vector_only:
                metadata["caption_relation"] = "page_derived_unresolved"
                metadata["caption_relation_confidence"] = "medium"
            crops.append(crop)
        return crops, len(crops)
