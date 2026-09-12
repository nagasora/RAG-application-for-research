"""Bounded scholarly metadata gateways used by discovery search.

Provider payloads stay in this module.  Callers receive a small normalized
snapshot and stable error code, never an upstream response or exception text.
"""
from __future__ import annotations

from html import unescape
import os
import re
import xml.etree.ElementTree as ET
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx

from .semantic_scholar import (
    LICENSE as SEMANTIC_SCHOLAR_LICENSE,
    PROVIDER as SEMANTIC_SCHOLAR_PROVIDER,
    RATE_LIMIT_POLICY as SEMANTIC_SCHOLAR_RATE_LIMIT_POLICY,
    SemanticScholarError,
    fetch_paper as fetch_semantic_paper,
    search_papers as semantic_search,
)

ProviderName = Literal["semantic_scholar", "openalex", "crossref", "cinii", "jstage"]
PROVIDERS: tuple[ProviderName, ...] = ("semantic_scholar", "openalex", "crossref", "cinii", "jstage")


class DiscoveryProviderError(Exception):
    def __init__(self, code: Literal["timeout", "rate_limited", "unavailable", "not_found", "invalid_response", "disabled"]):
        super().__init__(code)
        self.code = code


def _https_url(value: object) -> str:
    """Accept only absolute credential-free HTTPS public metadata links."""
    url = str(value or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.casefold() != "https" or not parts.netloc or not parts.hostname:
        return ""
    if parts.username is not None or parts.password is not None:
        return ""
    return url


def provider_enabled(provider: str) -> bool:
    # Japanese sources are opt-in because deployments may need institutional
    # terms/identifiers.  The public metadata sources need no local key.
    return provider != "cinii" or bool(os.getenv("CINII_APP_ID", "").strip())


def _get(url: str, *, params: dict, headers: dict | None = None, client: httpx.Client | None = None) -> dict:
    owned = client is None
    active = client or httpx.Client(timeout=10.0, headers=headers)
    try:
        try:
            response = active.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise DiscoveryProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise DiscoveryProviderError("unavailable") from exc
        if response.status_code == 429:
            raise DiscoveryProviderError("rate_limited")
        if response.status_code == 404:
            raise DiscoveryProviderError("not_found")
        if response.status_code >= 500:
            raise DiscoveryProviderError("unavailable")
        try:
            response.raise_for_status(); payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DiscoveryProviderError("invalid_response") from exc
        if not isinstance(payload, dict):
            raise DiscoveryProviderError("invalid_response")
        return payload
    finally:
        if owned:
            active.close()


def _abstract(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # OpenAlex inverted index format.
        positions = [(int(position), word) for word, indexes in value.items() if isinstance(indexes, list) for position in indexes if isinstance(position, int)]
        return " ".join(word for _, word in sorted(positions))
    return ""


def _plain_text(value: object) -> str:
    """Keep external abstracts readable while retaining the raw snapshot."""
    text = unescape(str(value or ""))
    return " ".join(re.sub(r"<[^>]*>", " ", text).split())


def _openalex(payload: dict) -> list[dict]:
    rows = payload.get("results")
    if not isinstance(rows, list): raise DiscoveryProviderError("invalid_response")
    result=[]
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip() or not str(row.get("title") or "").strip(): continue
        ids = {"openalex": str(row["id"]).rsplit("/", 1)[-1]}
        doi = str(row.get("doi") or "").strip()
        if doi: ids["doi"] = doi
        authors=[str(item.get("author", {}).get("display_name") or "").strip() for item in row.get("authorships", []) if isinstance(item, dict)]
        primary_location=row.get("primary_location") if isinstance(row.get("primary_location"),dict) else {}
        source=primary_location.get("source") if isinstance(primary_location.get("source"),dict) else {}
        result.append({"provider_paper_id": ids["openalex"], "title": str(row["title"]).strip(), "authors":[a for a in authors if a], "year":row.get("publication_year") if isinstance(row.get("publication_year"), int) else None, "publication_date":str(row.get("publication_date") or "") or None, "venue":str(source.get("display_name") or ""), "abstract":_abstract(row.get("abstract_inverted_index")), "citation_count":max(0, int(row.get("cited_by_count") or 0)), "external_ids":ids, "source_url":_https_url(row.get("id")), "language":str(row.get("language") or "") or None, "provider_snapshot":row})
    return result


def _crossref(payload: dict) -> list[dict]:
    message = payload.get("message", {}); rows = message.get("items") if isinstance(message, dict) else None
    if not isinstance(rows, list): raise DiscoveryProviderError("invalid_response")
    result=[]
    for row in rows:
        if not isinstance(row, dict): continue
        doi=str(row.get("DOI") or "").strip(); titles=row.get("title") or []
        title=str(titles[0] if isinstance(titles, list) and titles else "").strip()
        if not doi or not title: continue
        authors=[" ".join(part for part in (str(a.get("given") or "").strip(), str(a.get("family") or "").strip()) if part) for a in row.get("author", []) if isinstance(a, dict)]
        date_parts=((row.get("published-print") or row.get("published-online") or {}).get("date-parts") or [[None]])[0]
        year=date_parts[0] if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], int) else None
        result.append({"provider_paper_id":doi, "title":title, "authors":[a for a in authors if a], "year":year, "publication_date":None, "venue":str((row.get("container-title") or [""])[0] or ""), "abstract":str(row.get("abstract") or ""), "citation_count":max(0, int(row.get("is-referenced-by-count") or 0)), "external_ids":{"doi":doi}, "source_url":_https_url(row.get("URL")) or f"https://doi.org/{doi}"})
    return result


def verify_doi(doi: str, *, client: httpx.Client | None = None) -> dict:
    """Verify identity/license/correction metadata only; never retain abstracts."""
    normalized=doi.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/").removeprefix("doi:")
    if not normalized: raise DiscoveryProviderError("invalid_response")
    payload=_get(f"https://api.crossref.org/works/{normalized}", params={"mailto":os.getenv("CROSSREF_MAILTO", "").strip()} if os.getenv("CROSSREF_MAILTO", "").strip() else {}, headers={"User-Agent":"PaperPilot/1.0 (metadata verification)"}, client=client)
    message=payload.get("message")
    if not isinstance(message, dict): raise DiscoveryProviderError("invalid_response")
    return {"doi":str(message.get("DOI") or normalized), "license":[str(item.get("URL") or "") for item in message.get("license", []) if isinstance(item,dict)], "relations":message.get("relation") if isinstance(message.get("relation"),dict) else {}, "update_to":message.get("update-to") if isinstance(message.get("update-to"),list) else []}


_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def canonical_doi(value: object) -> str | None:
    """Return one stable DOI identifier, accepting the supported user forms.

    DOI identifiers are case-insensitive.  Using a lower-case canonical value
    keeps ``doi:``, ``doi.org`` URLs, and bare DOI input on the same durable
    deduplication key.
    """
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if re.match(r"^https?://", candidate, re.IGNORECASE):
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return None
        if parsed.scheme.casefold() not in {"http", "https"} or parsed.hostname not in {"doi.org", "www.doi.org"}:
            return None
        candidate = parsed.path.lstrip("/")
    else:
        candidate = re.sub(r"^doi:\s*", "", candidate, flags=re.IGNORECASE)
    if not _DOI_PATTERN.fullmatch(candidate) or any(char.isspace() for char in candidate):
        return None
    return candidate.casefold()


def _normalized_doi(value: object) -> str:
    return canonical_doi(value) or ""


def _crossref_doi_metadata(doi: str, *, client: httpx.Client | None = None) -> dict:
    params = {"mailto": os.getenv("CROSSREF_MAILTO", "").strip()} if os.getenv("CROSSREF_MAILTO", "").strip() else {}
    payload = _get(
        f"https://api.crossref.org/works/{quote(doi, safe='/')}", params=params,
        headers={"User-Agent": "PaperPilot/1.0 (DOI metadata import)"}, client=client,
    )
    snapshot = payload.get("message")
    if not isinstance(snapshot, dict):
        raise DiscoveryProviderError("invalid_response")
    canonical = canonical_doi(snapshot.get("DOI"))
    titles = snapshot.get("title")
    title = str(titles[0] if isinstance(titles, list) and titles else "").strip()
    if not canonical or canonical != doi or not title:
        raise DiscoveryProviderError("invalid_response")
    authors = [
        " ".join(part for part in (str(author.get("given") or "").strip(), str(author.get("family") or "").strip()) if part)
        for author in snapshot.get("author", []) if isinstance(author, dict)
    ]
    date_parts = ((snapshot.get("published-print") or snapshot.get("published-online") or snapshot.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = date_parts[0] if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], int) else None
    abstract = _plain_text(snapshot.get("abstract"))
    return {
        "provider": "crossref", "provider_paper_id": canonical,
        "external_ids": {"doi": canonical}, "title": title,
        "authors": [author for author in authors if author], "year": year,
        "abstract": abstract, "source_url": f"https://doi.org/{canonical}",
        "license": "crossref_api_metadata",
        "rate_limit_policy": "crossref_polite_pool" if params else "crossref_public_pool",
        "provider_snapshot": snapshot,
    }


def _semantic_doi_metadata(doi: str) -> dict:
    try:
        snapshot = fetch_semantic_paper(f"DOI:{doi}")
    except SemanticScholarError as exc:
        raise DiscoveryProviderError(exc.code) from exc
    if not isinstance(snapshot, dict):
        raise DiscoveryProviderError("invalid_response")
    title = str(snapshot.get("title") or "").strip()
    if not title:
        raise DiscoveryProviderError("invalid_response")
    raw_ids = snapshot.get("externalIds") or {}
    if not isinstance(raw_ids, dict):
        raise DiscoveryProviderError("invalid_response")
    returned_doi = next((value for key, value in raw_ids.items() if str(key).casefold() == "doi"), None)
    if returned_doi is not None and canonical_doi(returned_doi) != doi:
        raise DiscoveryProviderError("invalid_response")
    ids = {str(key).casefold(): str(value).strip() for key, value in raw_ids.items() if isinstance(value, (str, int)) and str(value).strip()}
    ids["doi"] = doi
    paper_id = str(snapshot.get("paperId") or "").strip()
    if paper_id:
        ids[SEMANTIC_SCHOLAR_PROVIDER] = paper_id
    authors = [str(author.get("name") or "").strip() for author in snapshot.get("authors", []) if isinstance(author, dict) and str(author.get("name") or "").strip()]
    return {
        "provider": SEMANTIC_SCHOLAR_PROVIDER, "provider_paper_id": paper_id or f"DOI:{doi}",
        "external_ids": ids, "title": title, "authors": authors,
        "year": snapshot.get("year") if isinstance(snapshot.get("year"), int) else None,
        "abstract": str(snapshot.get("abstract") or ""), "source_url": _https_url(snapshot.get("url")) or f"https://doi.org/{doi}",
        "license": SEMANTIC_SCHOLAR_LICENSE, "rate_limit_policy": SEMANTIC_SCHOLAR_RATE_LIMIT_POLICY,
        "provider_snapshot": snapshot,
    }


def fetch_doi_metadata(doi: str, *, client: httpx.Client | None = None) -> dict:
    """Fetch explicit DOI metadata from Crossref, then Semantic Scholar once.

    There is deliberately no retry loop: a user can safely retry a failed
    registration, while provider traffic and rate-limit behaviour stay clear.
    """
    canonical = canonical_doi(doi)
    if not canonical:
        raise DiscoveryProviderError("invalid_response")
    try:
        return _crossref_doi_metadata(canonical, client=client)
    except DiscoveryProviderError as crossref_error:
        try:
            return _semantic_doi_metadata(canonical)
        except DiscoveryProviderError as semantic_error:
            # Definitive Crossref absence and its rate limit remain actionable
            # even if the fallback is also unavailable.
            if crossref_error.code in {"not_found", "rate_limited"}:
                raise crossref_error
            raise semantic_error


def fetch_provider_paper(
    provider: ProviderName, provider_paper_id: str, external_ids: dict[str, str] | None = None, *,
    client: httpx.Client | None = None,
) -> dict:
    """Re-fetch one candidate and require a stable provider ID or DOI.

    Discovery sessions are short-lived presentation caches, not an authority
    for creating Papers. Providers without a singleton endpoint are queried
    narrowly and the returned identity is checked before any metadata is used.
    """

    if provider not in {"openalex", "cinii", "jstage"} or not provider_enabled(provider):
        raise DiscoveryProviderError("disabled")
    expected_id = str(provider_paper_id or "").strip()
    expected_doi = _normalized_doi((external_ids or {}).get("doi"))
    if not expected_id:
        raise DiscoveryProviderError("invalid_response")

    if provider == "openalex":
        openalex_id = expected_id.rsplit("/", 1)[-1]
        payload = _get(f"https://api.openalex.org/works/{openalex_id}", params={}, client=client)
        rows = _openalex({"results": [payload]})
    else:
        query = expected_doi or expected_id
        rows = search_provider(provider, query, limit=20, search_mode="keyword", client=client)

    for row in rows:
        actual_id = str(row.get("provider_paper_id") or "").strip()
        actual_ids = dict(row.get("external_ids") or {})
        actual_doi = _normalized_doi(actual_ids.get("doi"))
        actual_url = _https_url(actual_id)
        expected_url = _https_url(expected_id)
        if provider == "openalex":
            identity_matches = (
                actual_id.rsplit("/", 1)[-1].casefold()
                == expected_id.rsplit("/", 1)[-1].casefold()
            )
        else:
            identity_matches = actual_id == expected_id or bool(
                actual_url and expected_url and actual_url == expected_url
            )
        if identity_matches or (expected_doi and actual_doi == expected_doi):
            return row
    raise DiscoveryProviderError("invalid_response")


def _cinii(payload: dict) -> list[dict]:
    rows=payload.get("@graph") or payload.get("items") or []
    if not isinstance(rows,list): raise DiscoveryProviderError("invalid_response")
    result=[]
    for row in rows:
        if not isinstance(row,dict): continue
        identifier=str(row.get("@id") or row.get("id") or "").strip(); title=str(row.get("title") or "").strip()
        if not identifier or not title: continue
        doi=str(row.get("doi") or "").strip(); ids={"cinii":identifier};
        if doi: ids["doi"]=doi
        result.append({"provider_paper_id":identifier,"title":title,"authors":[str(x).strip() for x in row.get("creator",[]) if str(x).strip()] if isinstance(row.get("creator"),list) else [],"year":int(row["publicationDate"][:4]) if str(row.get("publicationDate") or "")[:4].isdigit() else None,"publication_date":str(row.get("publicationDate") or "") or None,"venue":str(row.get("publicationName") or ""),"abstract":"","citation_count":0,"external_ids":ids,"source_url":_https_url(row.get("@id"))})
    return result


def _jstage(xml_text: str) -> list[dict]:
    try: root=ET.fromstring(xml_text)
    except ET.ParseError as exc: raise DiscoveryProviderError("invalid_response") from exc
    ns={"a":"http://www.w3.org/2005/Atom","prism":"http://prismstandard.org/namespaces/basic/2.0/"}
    error=root.findtext(".//error") or root.findtext(".//errcode")
    if error and "ERR_003" in error: raise DiscoveryProviderError("rate_limited")
    if error and "ERR_001" in error: return []
    result=[]
    for entry in root.findall("a:entry",ns):
        identifier=(entry.findtext("a:id",default="",namespaces=ns) or "").strip(); title=(entry.findtext("a:title",default="",namespaces=ns) or "").strip()
        if not identifier or not title: continue
        doi=(entry.findtext("prism:doi",default="",namespaces=ns) or "").strip(); ids={"jstage":identifier};
        if doi: ids["doi"]=doi
        link=next((node.get("href") for node in entry.findall("a:link",ns) if node.get("href")), "")
        year=(entry.findtext("pubyear",default="") or "")
        result.append({"provider_paper_id":identifier,"title":title,"authors":[(x.text or "").strip() for x in entry.findall("a:author/a:name",ns) if (x.text or "").strip()],"year":int(year) if year.isdigit() else None,"publication_date":None,"venue":(entry.findtext("material_title",default="") or "").strip(),"abstract":(entry.findtext("abst",default="") or "").strip(),"citation_count":0,"external_ids":ids,"source_url":_https_url(link)})
    return result


def search_provider(provider: ProviderName, query: str, *, limit: int = 20, year_from: int | None = None, year_to: int | None = None, sort: str = "relevance", search_mode: str = "keyword", client: httpx.Client | None = None) -> list[dict]:
    if not provider_enabled(provider): raise DiscoveryProviderError("disabled")
    if provider == "semantic_scholar":
        try:
            payload=semantic_search(query, limit=limit, sort=sort if sort in {"relevance", "newest", "citation_count"} else "relevance", year_from=year_from, year_to=year_to, client=client)
        except SemanticScholarError as exc: raise DiscoveryProviderError(exc.code) from exc
        rows=[]
        for value in payload.get("data", []):
            if not isinstance(value, dict) or not str(value.get("paperId") or "").strip() or not str(value.get("title") or "").strip(): continue
            external={str(k).casefold():str(v).strip() for k,v in (value.get("externalIds") or {}).items() if str(v).strip()}; external["semantic_scholar"]=str(value["paperId"]).strip()
            rows.append({"provider_paper_id":str(value["paperId"]).strip(), "title":str(value["title"]).strip(), "authors":[str(a.get("name") or "").strip() for a in value.get("authors", []) if isinstance(a,dict) and str(a.get("name") or "").strip()], "year":value.get("year") if isinstance(value.get("year"),int) else None, "publication_date":str(value.get("publicationDate") or "") or None, "venue":str(value.get("venue") or ""), "abstract":str(value.get("abstract") or ""), "citation_count":max(0,int(value.get("citationCount") or 0)), "external_ids":external, "source_url":_https_url(value.get("url")), "provider_snapshot":value})
        return rows
    if provider == "openalex":
        filters=[]
        if year_from: filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to: filters.append(f"to_publication_date:{year_to}-12-31")
        mode="search.semantic" if search_mode == "question" else "search"
        payload=_get("https://api.openalex.org/works", params={mode:query,"per-page":min(limit,50), **({"filter":",".join(filters)} if filters else {})}, client=client)
        return _openalex(payload)
    if provider == "crossref":
        raise DiscoveryProviderError("disabled")
    if provider == "cinii":
        payload=_get("https://cir.nii.ac.jp/opensearch/v2/articles", params={"appid":os.environ["CINII_APP_ID"],"format":"json","q":query,"count":min(limit,200),"sortorder":{"relevance":4,"newest":0,"citation_count":10}.get(sort,4), **({"from":year_from} if year_from else {}), **({"until":year_to} if year_to else {})}, client=client)
        return _cinii(payload)
    if provider == "jstage":
        owned=client is None; active=client or httpx.Client(timeout=10.0)
        try:
            query_field="text" if search_mode == "question" else "article"
            response=active.get("https://api.jstage.jst.go.jp/searchapi/do", params={"service":3,query_field:query,"start":1,"count":min(limit,1000), **({"pubyearfrom":year_from} if year_from else {}), **({"pubyearto":year_to} if year_to else {})})
            if response.status_code==429: raise DiscoveryProviderError("rate_limited")
            response.raise_for_status(); return _jstage(response.text)
        except httpx.TimeoutException as exc: raise DiscoveryProviderError("timeout") from exc
        except httpx.HTTPError as exc: raise DiscoveryProviderError("unavailable") from exc
        finally:
            if owned: active.close()
    raise DiscoveryProviderError("disabled")
