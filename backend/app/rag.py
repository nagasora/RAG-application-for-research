from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import Iterable

from .models import Chunk, Citation, ComparisonRow, Paper


STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "に", "の", "は", "を", "が", "と", "で", "た", "する", "した", "して", "ある", "いる",
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    latin = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]+", text.lower())
    japanese = re.findall(r"[一-龠ぁ-んァ-ヶ]{2,}", text)
    bigrams = [word[i : i + 2] for word in japanese for i in range(max(0, len(word) - 1))]
    return [token for token in latin + japanese + bigrams if token not in STOP_WORDS]


def chunk_pages(pages: Iterable[tuple[int, str]], paper_id: str, size: int = 1100, overlap: int = 180) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page, raw in pages:
        text = normalize(raw)
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                boundary = max(text.rfind("。", start, end), text.rfind(". ", start, end))
                if boundary > start + size // 2:
                    end = boundary + 1
            chunks.append(Chunk(paper_id=paper_id, page=page, text=text[start:end]))
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks


def _score(query_tokens: list[str], text: str) -> float:
    doc_tokens = tokens(text)
    if not doc_tokens:
        return 0.0
    q = Counter(query_tokens)
    d = Counter(doc_tokens)
    common = set(q) & set(d)
    cosine = sum(q[t] * d[t] for t in common) / (
        math.sqrt(sum(v * v for v in q.values())) * math.sqrt(sum(v * v for v in d.values())) + 1e-9
    )
    phrase = 1.0 if normalize(" ".join(query_tokens)) in text.lower() else 0.0
    coverage = len(common) / max(1, len(set(query_tokens)))
    return 0.65 * cosine + 0.30 * coverage + 0.05 * phrase


def search(papers: list[Paper], query: str, limit: int = 8) -> list[tuple[Paper, Chunk, float]]:
    query_tokens = tokens(query)
    ranked: list[tuple[Paper, Chunk, float]] = []
    for paper in papers:
        for chunk in paper.chunks:
            score = _score(query_tokens, f"{paper.title} {paper.abstract} {chunk.text}")
            if score > 0:
                ranked.append((paper, chunk, score))
    return sorted(ranked, key=lambda item: item[2], reverse=True)[:limit]


def citations_from(results: list[tuple[Paper, Chunk, float]]) -> list[Citation]:
    return [
        Citation(
            index=index,
            paper_id=paper.id,
            paper_title=paper.title,
            chunk_id=chunk.id,
            page=chunk.page,
            section=chunk.section,
            excerpt=chunk.text[:360] + ("…" if len(chunk.text) > 360 else ""),
            score=round(score, 4),
        )
        for index, (paper, chunk, score) in enumerate(results, 1)
    ]


def extractive_answer(query: str, citations: list[Citation]) -> str:
    if not citations:
        return "登録済み論文から、この質問を裏付ける根拠を見つけられませんでした。検索語や対象論文を変更してください。"
    points = []
    for citation in citations[:4]:
        sentence = re.split(r"(?<=[。.!?])\s+", citation.excerpt)[0][:220]
        points.append(f"{sentence} [{citation.index}]")
    return f"「{query}」について、登録論文から確認できた主要な根拠は次のとおりです。\n\n" + "\n\n".join(points)


def llm_answer(query: str, citations: list[Citation]) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not citations:
        return None
    try:
        from openai import OpenAI

        context = "\n\n".join(
            f"[{c.index}] {c.paper_title}, p.{c.page}\n{c.excerpt}" for c in citations
        )
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            instructions=(
                "あなたは論文調査アシスタントです。提示された根拠だけで日本語回答を作成してください。"
                "各主張の末尾に必ず [1] の形式で根拠番号を付け、根拠がなければ不明と明記してください。"
            ),
            input=f"質問: {query}\n\n根拠:\n{context}",
        )
        return response.output_text
    except Exception:
        return None


def _find_sentence(text: str, keywords: list[str]) -> str:
    sentences = re.split(r"(?<=[。.!?])\s+|\n+", normalize(text))
    for sentence in sentences:
        lower = sentence.lower()
        if any(keyword in lower for keyword in keywords) and len(sentence) > 25:
            return sentence[:260]
    return sentences[0][:260] if sentences and sentences[0] else "記載なし"


def compare_papers(papers: list[Paper]) -> list[ComparisonRow]:
    rows = []
    for paper in papers:
        text = " ".join([paper.abstract] + [chunk.text for chunk in paper.chunks[:12]])
        rows.append(ComparisonRow(
            paper_id=paper.id,
            title=paper.title,
            purpose=_find_sentence(text, ["目的", "aim", "objective", "propose"]),
            method=_find_sentence(text, ["手法", "method", "model", "approach"]),
            results=_find_sentence(text, ["結果", "result", "improved", "outperform"]),
            limitations=_find_sentence(text, ["限界", "課題", "limitation", "future work"]),
        ))
    return rows


def research_gaps(papers: list[Paper]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    keywords = ["limitation", "limitations", "future work", "課題", "限界", "今後"]
    for paper in papers:
        for chunk in paper.chunks:
            sentence = _find_sentence(chunk.text, keywords)
            if sentence != "記載なし" and any(k in sentence.lower() for k in keywords):
                gaps.append({
                    "paper_id": paper.id,
                    "paper_title": paper.title,
                    "page": str(chunk.page),
                    "gap": sentence,
                    "opportunity": f"「{sentence[:90]}」を検証可能な仮説へ分解し、異なるデータセットまたは手法で再評価する。",
                })
                break
    return gaps
