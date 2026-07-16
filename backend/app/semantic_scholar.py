"""Small, injectable Semantic Scholar client; callers persist snapshots, not live views."""
from __future__ import annotations
import httpx

RATE_LIMIT_POLICY = "api_key_intro_1_rps"
LICENSE = "semantic_scholar_api"

def fetch_paper(paper_id: str, client: httpx.Client | None = None) -> dict:
    owned = client is None
    client = client or httpx.Client(timeout=10)
    try:
        response = client.get(f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}", params={"fields": "title,abstract,url,openAccessPdf"})
        response.raise_for_status()
        return response.json()
    finally:
        if owned: client.close()
