"""Pure discovery-session presentation helpers.

Provider I/O remains in ``discovery_providers`` and persistence remains in the
store. These helpers keep cursor/session response construction independent of
FastAPI so it is shared by the initial and replayed search paths.
"""
from __future__ import annotations

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from .models import DiscoverySearchItem
from .discovery_providers import _https_url, canonical_doi


class DiscoveryWorkflowStore(Protocol):
    def discovery_session_page(
        self, workspace_id: str, session_id: str, offset: int, limit: int = 20,
    ) -> tuple[list[Any], int, datetime]: ...

    def find_paper_by_external_identifiers(
        self, workspace_id: str, identifiers: list[tuple[str, str]],
    ) -> Any | None: ...


def session_page_items(store: DiscoveryWorkflowStore, workspace_id: str, session_id: str, offset: int, *, limit: int = 20) -> tuple[list[DiscoverySearchItem], int, datetime]:
    rows, total, expires_at = store.discovery_session_page(workspace_id, session_id, offset, limit)
    items: list[DiscoverySearchItem] = []
    for row in rows:
        snapshot = dict(row.snapshot or {})
        ids = dict(row.provider_ids or {})
        existing = store.find_paper_by_external_identifiers(workspace_id, list(ids.items()))
        items.append(DiscoverySearchItem(
            candidate_id=row.id, provider=row.provider, provider_paper_id=row.provider_paper_id,
            provider_ids=ids, source_providers=list(snapshot.get("source_providers") or [row.provider]),
            language=snapshot.get("language"), match_reasons=list(snapshot.get("match_reasons") or ["planned_query"]),
            possible_duplicate_of=None, title=str(snapshot.get("title") or ""),
            authors=list(snapshot.get("authors") or []), year=snapshot.get("year"),
            publication_date=snapshot.get("publication_date"), venue=snapshot.get("venue"),
            abstract=str(snapshot.get("abstract") or ""), citation_count=int(snapshot.get("citation_count") or 0),
            external_ids=ids, source_url=str(snapshot.get("source_url") or ""),
            existing_paper_id=existing.id if existing else None,
        ))
    return items, total, expires_at


def federate_provider_rows(body, fetch_provider_rows) -> tuple[list[dict], list, list[Exception], object | None]:
    """Run independent providers concurrently and merge their normalized rows.

    ``fetch_provider_rows`` owns the upstream-specific call and must return
    ``(provider, rows, status, failure, legacy_payload)``.  This service owns
    scheduling and RRF identity merge, so neither requires a FastAPI request.
    """
    with ThreadPoolExecutor(max_workers=len(body.providers)) as executor:
        provider_results = list(executor.map(fetch_provider_rows, body.providers))
    grouped: dict[str, dict] = {}
    statuses = []
    failures: list[Exception] = []
    legacy_payload = None
    for provider, rows, status, failure, provider_legacy_payload in provider_results:
        statuses.append(status)
        if failure is not None:
            failures.append(failure)
        if provider == "semantic_scholar" and provider_legacy_payload is not None:
            legacy_payload = provider_legacy_payload
        for row in rows:
            ids = {str(k).casefold(): str(v).strip() for k, v in dict(row.get("external_ids") or {}).items() if str(v).strip()}
            doi = canonical_doi(ids.get("doi")) or ""
            key = f"doi:{doi}" if doi else (f"arxiv:{ids['arxiv'].casefold()}" if ids.get("arxiv") else f"{provider}:{row['provider_paper_id']}")
            current = grouped.get(key)
            if current is None:
                snapshot = {
                    "title": row.get("title", ""), "authors": list(row.get("authors") or []),
                    "year": row.get("year"), "publication_date": row.get("publication_date"),
                    "abstract": row.get("abstract", ""), "venue": row.get("venue", ""),
                    "citation_count": row.get("citation_count", 0), "source_url": _https_url(row.get("source_url")),
                    "language": row.get("language"), "source_providers": [provider],
                    "match_reasons": ["planned_query"], "provider_snapshot": row.get("provider_snapshot", row),
                }
                grouped[key] = {**row, "provider": provider, "provider_ids": ids, "canonical_key": key, "source_providers": [provider], "match_reasons": ["planned_query"], "language": row.get("language"), "rrf_score": float(row.get("rrf") or 0), "snapshot": snapshot}
            else:
                current["provider_ids"].update(ids)
                current["source_providers"].append(provider)
                current["citation_count"] = max(current["citation_count"], row.get("citation_count", 0))
                current["rrf_score"] += float(row.get("rrf") or 0)
    ordered = sorted(grouped.values(), key=lambda item: (-float(item.get("rrf_score") or 0), str(item.get("title") or "").casefold()))[:80]
    return ordered, statuses, failures, legacy_payload
