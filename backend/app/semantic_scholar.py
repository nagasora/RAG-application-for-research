"""Small, injectable Semantic Scholar client.

The API layer persists a provider snapshot and never exposes provider response
details directly.  Keeping this boundary narrow also lets normal tests use an
``httpx.MockTransport`` without network access.
"""
from __future__ import annotations

import os
from typing import Literal

import httpx


RATE_LIMIT_POLICY = "api_key_intro_1_rps"
LICENSE = "semantic_scholar_api"
PROVIDER = "semantic_scholar"
TIMEOUT_SECONDS = 10.0
_PAPER_FIELDS = "paperId,title,authors,year,publicationDate,venue,abstract,citationCount,externalIds,url"


class SemanticScholarError(Exception):
    """A safe, stable description of an upstream failure."""

    def __init__(self, code: Literal["timeout", "rate_limited", "unavailable", "not_found", "invalid_response"]):
        super().__init__(code)
        self.code = code


def _client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    headers: dict[str, str] = {}
    if api_key := os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip():
        headers["x-api-key"] = api_key
    return httpx.Client(timeout=TIMEOUT_SECONDS, headers=headers), True


def _json(response: httpx.Response) -> dict:
    if response.status_code == 429:
        raise SemanticScholarError("rate_limited")
    if response.status_code == 404:
        raise SemanticScholarError("not_found")
    if response.status_code >= 500:
        raise SemanticScholarError("unavailable")
    try:
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise SemanticScholarError("timeout") from exc
    except httpx.HTTPError as exc:
        raise SemanticScholarError("unavailable") from exc
    except ValueError as exc:
        raise SemanticScholarError("invalid_response") from exc
    if not isinstance(payload, dict):
        raise SemanticScholarError("invalid_response")
    return payload


def fetch_paper(paper_id: str, client: httpx.Client | None = None) -> dict:
    """Fetch one canonical metadata snapshot by Semantic Scholar paper id."""
    active_client, owned = _client(client)
    try:
        try:
            response = active_client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}",
                params={"fields": _PAPER_FIELDS},
            )
        except httpx.TimeoutException as exc:
            raise SemanticScholarError("timeout") from exc
        except httpx.HTTPError as exc:
            raise SemanticScholarError("unavailable") from exc
        return _json(response)
    finally:
        if owned:
            active_client.close()


def search_papers(
    query: str,
    *,
    offset: int = 0,
    cursor_token: str | None = None,
    limit: int = 20,
    sort: Literal["relevance", "newest", "citation_count"] = "relevance",
    year_from: int | None = None,
    year_to: int | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Return one provider page.  ``offset`` is wrapped by our opaque cursor."""
    params: dict[str, str | int] = {
        "query": query,
        "limit": limit,
        "fields": _PAPER_FIELDS,
    }
    if year_from is not None or year_to is not None:
        params["year"] = f"{year_from or ''}-{year_to or ''}"
    active_client, owned = _client(client)
    try:
        endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"
        if sort == "relevance":
            params["offset"] = offset
        else:
            endpoint = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
            params["sort"] = "publicationDate:desc" if sort == "newest" else "citationCount:desc"
            if cursor_token:
                params["token"] = cursor_token
        try:
            response = active_client.get(endpoint, params=params)
        except httpx.TimeoutException as exc:
            raise SemanticScholarError("timeout") from exc
        except httpx.HTTPError as exc:
            raise SemanticScholarError("unavailable") from exc
        payload = _json(response)
        if not isinstance(payload.get("data"), list):
            raise SemanticScholarError("invalid_response")
        return payload
    finally:
        if owned:
            active_client.close()
