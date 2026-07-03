"""Wikidata awards lookup for people.

Resolves a person to a Wikidata entity via their TMDb person id (property P4985)
or IMDb id (property P345) and fetches award/nomination statements through the
public Wikidata SPARQL endpoint. Results are normalized to a stable schema that
DiscVault stores and contributes to MovieVault.

The module only depends on ``requests`` and fails soft: any network/parse error
returns an empty result instead of raising, so callers can treat awards as
best-effort enrichment.
"""

from __future__ import annotations

import re
from typing import Any

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "DiscVault/1.0 (awards enrichment; +https://discvault.app)"

# Wikidata properties used below:
#   P4985 = TMDb person ID, P345 = IMDb ID, P4947 = TMDb movie ID
#   P166  = award received, P1411 = nominated for
#   P585  = point in time,  P1686 = for work
_QID_RE = re.compile(r"Q\d+$")


def _qid_from_uri(uri: str) -> str:
    if not uri:
        return ""
    tail = str(uri).rstrip("/").rsplit("/", 1)[-1]
    return tail if _QID_RE.match(tail) else ""


def _year_from_time(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"^[+-]?(\d{4})", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _sparql_escape(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _run_sparql(query: str, *, timeout: float = 12.0) -> list[dict[str, Any]]:
    import requests

    response = requests.get(
        SPARQL_ENDPOINT,
        params={"query": query, "format": "json"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return (payload.get("results") or {}).get("bindings") or []


def resolve_wikidata_id(
    *,
    tmdb_id: Any = None,
    imdb_id: str | None = None,
    timeout: float = 12.0,
) -> str:
    """Return the Wikidata QID for a person from their TMDb or IMDb id."""
    tmdb = str(tmdb_id or "").strip()
    imdb = str(imdb_id or "").strip()
    clauses = []
    if tmdb:
        clauses.append(f'{{ ?person wdt:P4985 "{_sparql_escape(tmdb)}" }}')
    if imdb:
        clauses.append(f'{{ ?person wdt:P345 "{_sparql_escape(imdb)}" }}')
    if not clauses:
        return ""
    query = "SELECT ?person WHERE { " + " UNION ".join(clauses) + " } LIMIT 1"
    try:
        rows = _run_sparql(query, timeout=timeout)
    except Exception:
        return ""
    for row in rows:
        qid = _qid_from_uri((row.get("person") or {}).get("value") or "")
        if qid:
            return qid
    return ""


def _awards_query(qid: str, language: str) -> str:
    lang = re.sub(r"[^a-zA-Z\-]", "", str(language or "en")) or "en"
    return f"""
SELECT ?type ?award ?awardLabel ?time ?work ?workLabel ?workTmdb WHERE {{
  VALUES ?person {{ wd:{qid} }}
  {{
    ?person p:P166 ?st .
    ?st ps:P166 ?award .
    BIND("won" AS ?type)
    OPTIONAL {{ ?st pq:P585 ?time }}
    OPTIONAL {{ ?st pq:P1686 ?work . OPTIONAL {{ ?work wdt:P4947 ?workTmdb }} }}
  }} UNION {{
    ?person p:P1411 ?st .
    ?st ps:P1411 ?award .
    BIND("nominated" AS ?type)
    OPTIONAL {{ ?st pq:P585 ?time }}
    OPTIONAL {{ ?st pq:P1686 ?work . OPTIONAL {{ ?work wdt:P4947 ?workTmdb }} }}
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{lang},en". }}
}}
LIMIT 500
""".strip()


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    awards: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for row in rows:
        def val(key: str) -> str:
            return (row.get(key) or {}).get("value") or ""

        award_label = val("awardLabel")
        award_qid = _qid_from_uri(val("award"))
        if not award_label and not award_qid:
            continue
        result = val("type") or "won"
        year = _year_from_time(val("time"))
        work_label = val("workLabel")
        work_qid = _qid_from_uri(val("work"))
        # Skip an unlabeled work that only resolved to a bare QID.
        if work_label and _QID_RE.match(work_label):
            work_label = ""
        try:
            work_tmdb: int | None = int(val("workTmdb")) if val("workTmdb") else None
        except ValueError:
            work_tmdb = None
        dedupe = (award_qid or award_label, year, work_qid or work_label, result)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        awards.append(
            {
                "award": award_label or award_qid,
                "awardWikidataId": award_qid,
                "category": "",
                "year": year,
                "work": work_label,
                "workWikidataId": work_qid,
                "workTmdbId": work_tmdb,
                "result": "won" if result == "won" else "nominated",
                "source": "wikidata",
            }
        )
    return _collapse_won_over_nominated(awards)


def _collapse_won_over_nominated(awards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """If a win and a nomination share award/year/work, keep only the win."""
    won_keys = {
        (a["awardWikidataId"] or a["award"], a["year"], a["workWikidataId"] or a["work"])
        for a in awards
        if a["result"] == "won"
    }
    collapsed = []
    for award in awards:
        key = (award["awardWikidataId"] or award["award"], award["year"], award["workWikidataId"] or award["work"])
        if award["result"] == "nominated" and key in won_keys:
            continue
        collapsed.append(award)
    return collapsed


def fetch_person_awards(
    *,
    tmdb_id: Any = None,
    imdb_id: str | None = None,
    wikidata_id: str | None = None,
    language: str = "en",
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Fetch normalized awards for a person.

    Returns ``{"wikidataId": str, "awards": [..]}``. Always returns a dict; on
    any failure the awards list is empty.
    """
    qid = str(wikidata_id or "").strip()
    if not _QID_RE.match(qid):
        qid = resolve_wikidata_id(tmdb_id=tmdb_id, imdb_id=imdb_id, timeout=timeout)
    if not qid:
        return {"wikidataId": "", "awards": []}
    try:
        rows = _run_sparql(_awards_query(qid, language), timeout=timeout)
    except Exception:
        return {"wikidataId": qid, "awards": []}
    awards = _normalize_rows(rows)
    awards.sort(key=lambda a: (a["award"] or "", -(a["year"] or 0)))
    return {"wikidataId": qid, "awards": awards}


def group_awards(awards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group a flat award list by award name for display."""
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for award in awards or []:
        key = award.get("awardWikidataId") or award.get("award") or ""
        if key not in groups:
            groups[key] = {
                "award": award.get("award") or "",
                "awardWikidataId": award.get("awardWikidataId") or "",
                "items": [],
            }
            order.append(key)
        groups[key]["items"].append(award)
    result = []
    for key in order:
        group = groups[key]
        group["items"].sort(key=lambda a: -(a.get("year") or 0))
        group["wins"] = sum(1 for a in group["items"] if a.get("result") == "won")
        group["nominations"] = sum(1 for a in group["items"] if a.get("result") == "nominated")
        result.append(group)
    return result
