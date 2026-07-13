"""Bounded, dependency-free parsers for provenance-preserving research sources."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from io import StringIO
from typing import Literal


class SourceParseLimitError(ValueError):
    pass


@dataclass(frozen=True)
class SourceParseLimits:
    max_input_bytes: int = 5 * 1024 * 1024
    max_lines: int = 100_000
    max_spans: int = 10_000


@dataclass(frozen=True)
class AtomicSpan:
    kind: str
    locator: str
    text: str
    metadata: dict = field(default_factory=dict)
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    cell_index: object | None = None


@dataclass(frozen=True)
class SourceParseResult:
    source_kind: str
    metadata: dict
    spans: list[AtomicSpan]


def _text(data: bytes | str, limits: SourceParseLimits) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > limits.max_input_bytes:
        raise SourceParseLimitError(f"source exceeds {limits.max_input_bytes} bytes")
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if text.count("\n") + 1 > limits.max_lines:
        raise SourceParseLimitError(f"source exceeds {limits.max_lines} lines")
    return text


def _bounded(spans: list[AtomicSpan], limits: SourceParseLimits) -> list[AtomicSpan]:
    if len(spans) > limits.max_spans:
        raise SourceParseLimitError(f"source produces more than {limits.max_spans} spans")
    return spans


def parse_latex(data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    limits = limits or SourceParseLimits(); text = _text(data, limits)
    pattern = re.compile(r"\\\[(.*?)\\\]|\$\$(.*?)\$\$|\\begin\{(equation\*?|align\*?)\}(.*?)\\end\{\3\}|(?<!\\)\$(.+?)(?<!\\)\$", re.S)
    spans: list[AtomicSpan] = []
    for index, match in enumerate(pattern.finditer(text), 1):
        formula = next((part for part in match.groups() if part is not None and part not in {"equation", "equation*", "align", "align*"}), "").strip()
        start_line = text.count("\n", 0, match.start()) + 1
        spans.append(AtomicSpan("math", f"math:{index}", formula, {"delimiter": match.group(0)[:2]}, line_start=start_line, line_end=start_line + match.group(0).count("\n"), char_start=match.start(), char_end=match.end()))
    return SourceParseResult("latex", {"formula_count": len(spans), "parse_error": text.count("$") % 2 == 1}, _bounded(spans, limits))


def parse_python(data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    limits = limits or SourceParseLimits(); text = _text(data, limits)
    try: tree = ast.parse(text)
    except SyntaxError as exc: raise ValueError(f"invalid Python syntax at line {exc.lineno}") from exc
    lines = text.splitlines(); spans: list[AtomicSpan] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)) and hasattr(node, "lineno"):
            end = getattr(node, "end_lineno", node.lineno); snippet = "\n".join(lines[node.lineno - 1:end])
            kind = "function" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "class" if isinstance(node, ast.ClassDef) else "assignment"
            name = getattr(node, "name", None) or (ast.unparse(node.targets[0]) if isinstance(node, ast.Assign) else "assignment")
            digest = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
            spans.append(AtomicSpan(kind, f"{kind}:{name}", snippet, {"ast_hash": digest, "name": name}, line_start=node.lineno, line_end=end))
    return SourceParseResult("python", {"ast_hash": hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()}, _bounded(sorted(spans, key=lambda item: (item.line_start or 0, item.locator)), limits))


def parse_notebook(data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    limits = limits or SourceParseLimits(); text = _text(data, limits)
    try: notebook = json.loads(text)
    except json.JSONDecodeError as exc: raise ValueError("invalid Jupyter notebook JSON") from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list): raise ValueError("notebook must contain cells")
    spans: list[AtomicSpan] = []
    for index, cell in enumerate(notebook["cells"]):
        if not isinstance(cell, dict): continue
        source = cell.get("source", ""); source = "".join(source) if isinstance(source, list) else str(source)
        if not source.strip(): continue
        metadata = cell.get("metadata") if isinstance(cell.get("metadata"), dict) else {}
        execution = cell.get("execution_count")
        cell_id = str(cell.get("id") or f"cell-{index}")
        spans.append(AtomicSpan(str(cell.get("cell_type") or "raw"), f"cell:{cell_id}", source, {"cell_id": cell_id, "execution_count": execution, "metadata": metadata}, line_start=1, line_end=source.count("\n") + 1, cell_index={"index": index, "id": cell_id, "execution_count": execution}))
    return SourceParseResult("notebook", {"nbformat": notebook.get("nbformat"), "cell_count": len(notebook["cells"])}, _bounded(spans, limits))


def parse_csv(data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    limits = limits or SourceParseLimits(); text = _text(data, limits)
    rows = list(csv.reader(StringIO(text)))
    if not rows: raise ValueError("CSV has no rows")
    headers = rows[0]; spans: list[AtomicSpan] = []
    for row_number, row in enumerate(rows[1:], 2):
        values = {headers[index] if index < len(headers) and headers[index] else f"column_{index + 1}": value for index, value in enumerate(row)}
        spans.append(AtomicSpan("csv_row", f"row:{row_number}", json.dumps(values, ensure_ascii=False), {"headers": headers}, line_start=row_number, line_end=row_number, cell_index={"row": row_number, "columns": list(values)}))
    return SourceParseResult("csv", {"headers": headers, "row_count": len(rows) - 1}, _bounded(spans, limits))


def _chat_content_text(content: object) -> str:
    """Normalize supported chat content without stringifying opaque blocks.

    OpenAI exports commonly encode a message as a list of ``{"type":
    "text", "text": "..."}`` blocks.  Treating unknown blocks as strings
    would make an image/tool payload look like grounded research text, so this
    boundary is intentionally strict.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("chat message content must be a string or a list of text blocks")

    parts: list[str] = []
    for block in content:
        # Preserve the historical list[str] representation used by simple chat
        # exports while also accepting OpenAI's structured text blocks.
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            raise ValueError("chat content block must be a string or an object")
        block_type = block.get("type")
        if block_type not in {"text", "input_text", "output_text"}:
            raise ValueError("unsupported chat content block type")
        block_text = block.get("text")
        if not isinstance(block_text, str):
            raise ValueError("chat text content block requires a string text field")
        parts.append(block_text)
    return "".join(parts)


def parse_chat_json(data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    limits = limits or SourceParseLimits(); text = _text(data, limits)
    try: value = json.loads(text)
    except json.JSONDecodeError as exc: raise ValueError("invalid chat JSON") from exc
    messages = value.get("messages", value) if isinstance(value, dict) else value
    if not isinstance(messages, list): raise ValueError("chat JSON must be a message list")
    spans: list[AtomicSpan] = []
    for index, item in enumerate(messages, 1):
        if not isinstance(item, dict):
            raise ValueError("chat message must be an object")
        content = _chat_content_text(item["content"] if "content" in item else item.get("text", ""))
        if content.strip(): spans.append(AtomicSpan("chat_turn", f"turn:{index}", content, {"role": str(item.get("role", "unknown")), "timestamp": item.get("timestamp")}, cell_index={"ordinal": index}))
    return SourceParseResult("chat", {"turn_count": len(spans), "format": "json"}, _bounded(spans, limits))


def parse_chat_markdown(data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    limits = limits or SourceParseLimits(); text = _text(data, limits)
    parts = re.split(r"(?=^#{1,4}\s*(?:user|assistant|system)|^\*\*(?:user|assistant|system)\*\*\s*:?)", text, flags=re.I | re.M)
    spans: list[AtomicSpan] = []
    for index, part in enumerate((part.strip() for part in parts if part.strip()), 1):
        role = re.match(r"(?:#{1,4}\s*|\*\*)(user|assistant|system)", part, re.I)
        content = re.sub(r"^(?:#{1,4}\s*|\*\*)(?:user|assistant|system)(?:\*\*)?\s*:?\s*", "", part, flags=re.I)
        spans.append(AtomicSpan("chat_turn", f"turn:{index}", content or part, {"role": role.group(1).lower() if role else "unknown"}, cell_index={"ordinal": index}))
    return SourceParseResult("chat", {"turn_count": len(spans), "format": "markdown"}, _bounded(spans, limits))


def parse_chat(data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    text = _text(data, limits or SourceParseLimits())
    return parse_chat_json(text, limits=limits) if text.lstrip().startswith(("{", "[")) else parse_chat_markdown(text, limits=limits)


def parse_markdown(data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    limits = limits or SourceParseLimits(); text = _text(data, limits)
    blocks = re.split(r"\n\s*\n+", text)
    spans: list[AtomicSpan] = []
    offset = 0
    for index, block in enumerate(blocks, 1):
        start = text.find(block, offset)
        offset = max(offset, start + len(block))
        content = block.strip()
        if not content:
            continue
        line_start = text.count("\n", 0, start) + 1
        heading = re.match(r"^#{1,6}\s+(.+)$", content)
        spans.append(AtomicSpan(
            "markdown_heading" if heading else "markdown_block",
            f"block:{index}",
            content,
            {"heading": heading.group(1).strip() if heading else None},
            line_start=line_start,
            line_end=line_start + content.count("\n"),
            char_start=start,
            char_end=start + len(block),
        ))
    return SourceParseResult("markdown", {"block_count": len(spans)}, _bounded(spans, limits))


def parse_source(source_format: Literal["latex", "python", "notebook", "csv", "chat", "markdown"] | str, data: bytes | str, *, limits: SourceParseLimits | None = None) -> SourceParseResult:
    name = source_format.lower().strip()
    parsers = {"latex": parse_latex, "tex": parse_latex, "python": parse_python, "py": parse_python, "notebook": parse_notebook, "ipynb": parse_notebook, "csv": parse_csv, "chat": parse_chat, "chat_json": parse_chat_json, "chat_markdown": parse_chat_markdown, "markdown": parse_markdown, "md": parse_markdown}
    parser = parsers.get(name)
    if parser is None: raise ValueError(f"unsupported source format: {source_format}")
    return parser(data, limits=limits)
