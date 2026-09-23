"""Find existing IRIs for every concept the CV mentions.

Queries ROR (organizations), ESCO (occupations), OLS (EDAM, Software Ontology, NCBI Taxonomy),
Wikidata (everything, via full-text search), and the Library of Congress ISO 639-1 list
(languages). Writes the top candidates per term to output/iri-candidates.yml for review; the
chosen IRIs are then recorded in the data files.

Usage: python3 scripts/find-iris.py [--limit N]
"""

from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml

# Prefer IPv4 when both address families are available, as in update_cv_data.py.
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *args, **kwargs: [r for r in _getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET] or _getaddrinfo(*args, **kwargs)

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {
    "User-Agent": "jmillanacosta-cv-iri-finder/1.0 (https://jmillanacosta.github.io)",
    "Accept": "application/json",
}
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 5


def get(url: str, attempts: int = 4) -> Any:
    """GET JSON, retrying with backoff; rate-limited responses must not read as "no results"."""
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        time.sleep(2**attempt)
    print(f"  failed after {attempts} attempts: {url[:120]}")
    return None


# Wikidata asks clients to keep to one request at a time.
_wikidata_lock = threading.Lock()


def wikidata_get(url: str) -> Any:
    with _wikidata_lock:
        result = get(url)
        time.sleep(0.2)
        return result


def sparql(query: str) -> list[dict]:
    data = wikidata_get("https://query.wikidata.org/sparql?" + q({"query": query, "format": "json"}))
    return (data or {}).get("results", {}).get("bindings", [])


def q(params: dict) -> str:
    return urllib.parse.urlencode(params)


def wikidata(term: str) -> list[dict]:
    data = wikidata_get(
        "https://www.wikidata.org/w/api.php?"
        + q({"action": "query", "list": "search", "srsearch": term, "srlimit": LIMIT, "format": "json"})
    )
    hits = (data or {}).get("query", {}).get("search", [])
    ids = [hit["title"] for hit in hits]
    if not ids:
        return []
    entities = wikidata_get(
        "https://www.wikidata.org/w/api.php?"
        + q({"action": "wbgetentities", "ids": "|".join(ids), "props": "labels|descriptions", "languages": "en", "format": "json"})
    )
    rows = []
    for qid in ids:
        entity = (entities or {}).get("entities", {}).get(qid, {})
        rows.append(
            {
                "iri": f"http://www.wikidata.org/entity/{qid}",
                "label": entity.get("labels", {}).get("en", {}).get("value"),
                "description": entity.get("descriptions", {}).get("en", {}).get("value"),
            }
        )
    return rows


def ols(term: str, ontologies: str) -> list[dict]:
    data = get("https://www.ebi.ac.uk/ols4/api/search?" + q({"q": term, "ontology": ontologies, "rows": LIMIT, "exact": "false"}))
    return [
        {
            "iri": doc["iri"],
            "label": doc.get("label"),
            "description": (doc.get("description") or [None])[0],
            "source": doc.get("ontology_prefix"),
        }
        for doc in (data or {}).get("response", {}).get("docs", [])
    ]


def esco(term: str) -> list[dict]:
    data = get("https://ec.europa.eu/esco/api/search?" + q({"text": term, "type": "occupation", "language": "en", "limit": LIMIT}))
    return [{"iri": r["uri"], "label": r.get("title")} for r in (data or {}).get("_embedded", {}).get("results", [])]


def ror(term: str) -> list[dict]:
    data = get("https://api.ror.org/v2/organizations?" + q({"query": term}))
    rows = []
    for item in (data or {}).get("items", [])[:LIMIT]:
        name = next((n["value"] for n in item["names"] if "ror_display" in n["types"]), None)
        place = (item.get("locations") or [{}])[0].get("geonames_details", {})
        rows.append({"iri": item["id"], "label": name, "description": f"{place.get('name')}, {place.get('country_name')}"})
    return rows


def iso639(code: str) -> list[dict]:
    iri = f"http://id.loc.gov/vocabulary/iso639-1/{code}"
    return [{"iri": iri, "label": code}] if get(iri + ".json") is not None else []


SOURCES = {
    "occupation": lambda t: {"esco": esco(t), "wikidata": wikidata(t)},
    "organization": lambda t: {"ror": ror(t), "wikidata": wikidata(t)},
    "place": lambda t: {"wikidata": wikidata(t)},
    "language": lambda t: {"iso639-1": iso639(t.split("|")[1]), "wikidata": wikidata(t.split("|")[0] + " language")},
    "skill": lambda t: {"ols": ols(t, "edam,swo"), "wikidata": wikidata(t)},
    "taxon": lambda t: {"ols": ols(t, "ncbitaxon"), "wikidata": wikidata(t)},
    "event": lambda t: {"wikidata": wikidata(t)},
    "project": lambda t: {"wikidata": wikidata(t)},
    "journal": lambda t: {"wikidata": wikidata(t)},
    "degree": lambda t: {"wikidata": wikidata(t)},
    "field": lambda t: {"ols": ols(t, "edam"), "wikidata": wikidata(t)},
    "software": lambda t: {"wikidata": wikidata(t), "ols": ols(t, "swo")},
}

SKILL_TERMS = [
    "RDF", "OWL", "SPARQL", "SHACL", "VoID vocabulary", "LinkML", "ROBOT ontology tool", "Ontology Development Kit",
    "RDFLib", "GraphDB", "Model Context Protocol", "SSSOM", "pandas software", "Polars dataframe", "Pydantic",
    "Python programming language", "PyPI", "Sphinx documentation generator", "Flask web framework", "FastAPI",
    "Vue.js", "JavaScript", "TypeScript", "Cytoscape.js", "pytest", "tox", "Ruff linter", "mypy", "GitHub Actions",
    "GitLab CI", "Jenkins software", "Docker software", "Slurm Workload Manager", "knowledge graph",
    "ontology engineering", "data integration", "identifier mapping", "R Shiny", "Power BI", "ELISA",
    "RNA extraction", "BLAST", "SPSS", "R programming language",
]
DEGREES = ["Master of Science", "Bachelor of Science", "Doctor of Philosophy"]
FIELDS = ["bioinformatics", "biotechnology"]
OCCUPATIONS = ["doctoral researcher", "PhD candidate", "researcher", "research intern"]


def inventory() -> dict[str, list[str]]:
    cv = yaml.safe_load((ROOT / "_data/cv.yml").read_text(encoding="utf-8"))
    events = yaml.safe_load((ROOT / "_data/events.yml").read_text(encoding="utf-8"))
    publications = yaml.safe_load((ROOT / "_data/publications.yml").read_text(encoding="utf-8"))
    places = {o["location"] for o in cv["organizations"].values() if o.get("location")}
    places |= {e["location"] for e in events if e.get("location") and e["location"] != "Online"}
    places.add(f"{cv['person']['address']['locality']}, {cv['person']['address']['country']}")
    return {
        "occupation": OCCUPATIONS,
        "organization": sorted({o["name"] for o in cv["organizations"].values()} | {"RECETOX", "Masaryk University"}),
        "place": sorted(p.split(",")[0] for p in places),
        "language": [f"{lang['name']}|{lang['code']}" for lang in cv["languages"]],
        "skill": SKILL_TERMS,
        "taxon": sorted({t for e in cv["education"] for t in e.get("taxa", [])}),
        "event": sorted({e["name"] for e in events}),
        "project": sorted({p["name"] for p in cv["funded_projects"] + cv["communities"]}),
        "journal": sorted({p["journal"] for p in publications if p.get("journal")}),
        "degree": DEGREES,
        "field": FIELDS,
        "software": sorted({s["name"] for s in cv["libraries"] + cv["contributions"] + cv["personal_tools"]}),
    }


def by_identifier(prop: str, values: list[str]) -> dict[str, list[dict]]:
    """Exact Wikidata matches for identifiers the CV already holds (ROR, ISO 639, DOI, ...)."""
    if not values:
        return {}
    listed = " ".join(json.dumps(v) for v in values)
    rows = sparql(
        "SELECT ?value ?item ?itemLabel ?itemDescription WHERE { VALUES ?value { " + listed + " } "
        f"?item wdt:{prop} ?value . SERVICE wikibase:label {{ bd:serviceParam wikibase:language \"en\". }} }}"
    )
    found: dict[str, list[dict]] = {}
    for row in rows:
        found.setdefault(row["value"]["value"], []).append(
            {
                "iri": row["item"]["value"],
                "label": row.get("itemLabel", {}).get("value"),
                "description": row.get("itemDescription", {}).get("value"),
            }
        )
    return found


def issn(doi: str) -> list[str]:
    data = get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    return (data or {}).get("message", {}).get("ISSN", [])


def exact_matches() -> dict:
    cv = yaml.safe_load((ROOT / "_data/cv.yml").read_text(encoding="utf-8"))
    publications = yaml.safe_load((ROOT / "_data/publications.yml").read_text(encoding="utf-8"))
    rors = [o["ror"].rsplit("/", 1)[1] for o in cv["organizations"].values() if o.get("ror")] + ["032hzqh22", "02j46qs45"]
    repositories = [s["repository"] for s in cv["libraries"] + cv["contributions"] + cv["personal_tools"] if s.get("repository")]
    dois = [p["doi"].upper() for p in publications if p.get("doi")]
    journals = {}
    for pub in publications:
        if pub.get("journal") and pub.get("doi"):
            journals.setdefault(pub["journal"], set()).update(issn(pub["doi"]))
    issns = sorted({i for values in journals.values() for i in values})
    by_issn = by_identifier("P236", issns)
    taxa = {}
    for term in sorted({t for e in cv["education"] for t in e.get("taxa", [])}):
        match = next((d for d in ols(term, "ncbitaxon") if (d.get("label") or "").lower() == term.lower()), None)
        if match:
            taxa[term] = re.sub(r".*NCBITaxon_", "", match["iri"])
    return {
        "person (ORCID, P496)": by_identifier("P496", [cv["person"]["orcid"]]),
        "organizations (ROR, P6782)": by_identifier("P6782", rors),
        "languages (ISO 639-1, P218)": by_identifier("P218", [lang["code"] for lang in cv["languages"]]),
        "publications (DOI, P356)": by_identifier("P356", dois),
        "journals (ISSN, P236)": {j: [row for i in sorted(v) for row in by_issn.get(i, [])] for j, v in journals.items()},
        "taxa (NCBI Taxonomy, P685)": {**by_identifier("P685", list(taxa.values())), **{f"{k} (NCBITaxon)": [{"iri": f"http://purl.obolibrary.org/obo/NCBITaxon_{v}"}] for k, v in taxa.items()}},
        "software (repository, P1324)": by_identifier("P1324", repositories),
    }


def main() -> None:
    jobs = [(kind, term) for kind, terms in inventory().items() for term in terms]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda job: SOURCES[job[0]](job[1]), jobs))
    report: dict = {"exact": exact_matches()}
    for (kind, term), found in zip(jobs, results, strict=True):
        report.setdefault(kind, {})[term.split("|")[0]] = {source: rows for source, rows in found.items() if rows}
    out = ROOT / "output/iri-candidates.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    empty = [f"{kind}: {term}" for kind, terms in report.items() for term, found in terms.items() if not found]
    print(f"{len(jobs)} terms searched; candidates in {out.relative_to(ROOT)}")
    if empty:
        print("No candidates for: " + "; ".join(empty))


if __name__ == "__main__":
    main()
