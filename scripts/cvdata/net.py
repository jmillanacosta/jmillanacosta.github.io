"""The services that give the data (ORCID, GitHub, Wikidata and others), and the cache of slow lookups."""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from rdfsolve.sparql_helper import EndpointError, SparqlHelper

from .config import CACHE_DAYS, CACHE_FILE, HEADERS

# Prefer IPv4 when both address families are available (IPv6 routes can hang).
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *args, **kwargs: [r for r in _getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET] or _getaddrinfo(*args, **kwargs)


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_optional(url: str) -> dict | None:
    """Usage figures are best effort: a missing record or a failing service leaves them out."""
    try:
        return fetch_json(url)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"  skipped {url}: {error}")
        return None


def live(url: str | None) -> str | None:
    """The URL if it resolves, so dead links in package metadata are not published."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]}), timeout=30):
            return url
    except (urllib.error.URLError, TimeoutError, ValueError):
        print(f"  dropped a link that does not resolve: {url}")
        return None


def github(path: str) -> Any:
    """The GitHub API, authenticated when GITHUB_TOKEN (or GH_TOKEN) is set: unauthenticated
    calls are limited to 60 an hour."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {**HEADERS, "Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com{path}", headers=headers), timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def github_raw(repo: str, path: str) -> str | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/HEAD/{path}", headers=HEADERS), timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def github_repo(url: str | None) -> str | None:
    match = re.match(r"https://github\.com/([^/]+/[^/#?]+)", url or "")
    return match.group(1).removesuffix(".git") if match else None


# Wikidata is asked with SPARQL through rdfsolve. wdt: values are the best-ranked statements.
# Since the graph split of 2025, scholarly articles are in a separate query service; people,
# organizations, journals and everything else are in the main one.
WIKIDATA = SparqlHelper("https://query.wikidata.org/sparql", timeout=60)
SCHOLARLY = SparqlHelper("https://query-scholarly.wikidata.org/sparql", timeout=60)
WD = "http://www.wikidata.org/entity/"


def wikidata_rows(query: str, *, scholarly: bool = False) -> list[dict[str, str]]:
    """The rows of a Wikidata query, with plain values. Scholarly articles are in their own graph."""
    rows = (SCHOLARLY if scholarly else WIKIDATA).select(query)["results"]["bindings"]
    return [{name: cell["value"] for name, cell in row.items()} for row in rows]


def items(qids: list[str]) -> str:
    return " ".join(f"wd:{qid}" for qid in qids)


def wikidata_item(prop: str, value: str, *, scholarly: bool = False) -> str | None:
    """The one Wikidata item with this identifier (for example P496, an ORCID), if only one has it."""
    try:
        query = f"SELECT ?item WHERE {{ ?item wdt:{prop} {json.dumps(value)} }} LIMIT 2"
        rows = wikidata_rows(query, scholarly=scholarly)
    except EndpointError:
        return None
    return rows[0]["item"].removeprefix(WD) if len(rows) == 1 else None


def wikidata_facts(qids: list[str]) -> list[dict[str, Any]]:
    """For each item: its IRI, English label, ORCID (P496), GitHub username (P2037), identifiers,
    and official websites (P856), one row for each website (or one row without)."""
    rows = []
    for start in range(0, len(qids), 50):
        batch = qids[start : start + 50]
        found = wikidata_rows(f"""SELECT ?item ?label ?site ?prop ?value WHERE {{
  VALUES ?item {{ {items(batch)} }}
  {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
  UNION {{ ?item wdt:P856 ?site }}
  UNION {{ ?item ?direct ?value . ?property wikibase:directClaim ?direct; wikibase:propertyType wikibase:ExternalId .
          BIND(STRAFTER(STR(?property), STR(wd:)) AS ?prop) }}
}}""")
        for qid in batch:
            found_here = [r for r in found if r["item"] == WD + qid]
            if not found_here:
                continue
            ids = sorted({(r["prop"], r["value"]) for r in found_here if "prop" in r})
            base: dict[str, Any] = {"item": WD + qid, "ids": [list(i) for i in ids]}
            label = next((r["label"] for r in found_here if "label" in r), None)
            for key, value in (("label", label), ("orcid", dict(ids).get("P496")), ("gh", dict(ids).get("P2037"))):
                if value:
                    base[key] = value
            sites = sorted({r["site"] for r in found_here if "site" in r})
            rows += [{**base, "site": site} for site in sites] or [base]
    return rows


def wikidata_statements(qids: list[str]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, dict[tuple[str, str], dict[str, Any]]] = {qid: {} for qid in qids}
    for start in range(0, len(qids), 20):
        for r in wikidata_rows(statements_query(qids[start : start + 20])):
            r = {k: v.replace("http://schema.org/", "https://schema.org/") for k, v in r.items() if v}
            statement = found[r["item"].removeprefix(WD)].setdefault((r["property"], r["value"]), {
                "property": r["property"], "label": r["label"], "equivalent": [], "value": r.get("exact", r["value"]),
                **{key: r[name] for key, name in (("value_label", "valueLabel"), ("language", "language"), ("datatype", "datatype")) if name in r},
            })
            if r.get("equivalent") and r["equivalent"] not in statement["equivalent"]:
                statement["equivalent"].append(r["equivalent"])
            if r.get("type"):
                statement["value_type"] = r["type"]
    return {qid: sorted(rows.values(), key=lambda s: (s["property"], s["value"])) for qid, rows in found.items()}


def statements_query(qids: list[str]) -> str:
    return f"""SELECT ?item ?property ?label ?equivalent ?value ?valueLabel ?language ?datatype ?exact ?type WHERE {{
  VALUES ?item {{ {items(qids)} }}
  ?item ?direct ?value .
  ?property wikibase:directClaim ?direct; wikibase:propertyType ?kind; rdfs:label ?label
  FILTER(LANG(?label) = "en" && ?kind != wikibase:ExternalId)
  OPTIONAL {{ ?property wdt:P1647* ?super . ?super wdt:P1628 ?equivalent FILTER(REGEX(STR(?equivalent), "^https?://schema.org/")) }}
  OPTIONAL {{ ?value rdfs:label ?valueLabel FILTER(LANG(?valueLabel) = "en") }}
  OPTIONAL {{ ?value wdt:P1709|wdt:P2888 ?exact FILTER(REGEX(STR(?exact), "^https?://schema.org/")) }}
  OPTIONAL {{ ?value wdt:P31/(wdt:P1709|wdt:P2888) ?type FILTER(REGEX(STR(?type), "^https?://schema.org/")) }}
  BIND(LANG(?value) AS ?language)
  BIND(DATATYPE(?value) AS ?datatype)
}}"""


def wikidata_formatters(props: list[str]) -> dict[str, dict[str, str]]:
    """For identifier properties: the English label and the URL pattern (P1630) that makes a link
    from a value, when the property has one."""
    found: dict[str, dict[str, str]] = {}
    for start in range(0, len(props), 50):
        rows = wikidata_rows(f"""SELECT ?property ?label ?pattern WHERE {{
  VALUES ?property {{ {items(props[start : start + 50])} }}
  ?property wdt:P1630 ?pattern; rdfs:label ?label FILTER(LANG(?label) = "en")
}} ORDER BY ?pattern""")
        for row in rows:
            found.setdefault(row["property"].removeprefix(WD), {"label": row["label"], "pattern": row["pattern"]})
    return found


class Cache:
    def __init__(self, refresh: bool = False):
        self.entries: dict[str, dict] = {} if refresh or not CACHE_FILE.exists() else json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        self.today = time.strftime("%Y-%m-%d")

    def fresh(self, key: str) -> bool:
        entry = self.entries.get(key)
        return bool(entry) and time.mktime(time.strptime(entry["checked"], "%Y-%m-%d")) > time.time() - CACHE_DAYS * 86400

    def put(self, key: str, value: Any) -> None:
        self.entries[key] = {"checked": self.today, "value": value}

    def get(self, key: str, compute: Any) -> Any:
        """The cached value for key, computed again when missing or stale."""
        if not self.fresh(key):
            self.put(key, compute())
        return self.entries[key]["value"]

    def save(self) -> None:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(self.entries, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
