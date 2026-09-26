"""Identifier candidates are fetched for the configured CV and saved for review."""

import json
import re
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
from rdfsolve.api import OntologyLookup
from cvdata import wikidata

ROOT = Path(__file__).resolve().parent.parent
AGENT = f"cv-iri-finder/1.0 ({yaml.safe_load((ROOT / '_config.yml').read_text(encoding='utf-8'))['url']})"
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 5
OLS = OntologyLookup(max_requests=1000)
# Wikidata asks for one request at a time.
_wikidata_lock = threading.Lock()


def get(url: str, attempts: int = 4) -> Any:
    """A failed request is retried before it is reported."""
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
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


def candidates(iris: list[str], *, scholarly: bool = False) -> list[dict]:
    """The English label and description of each item, read through the rdfsolve client."""
    records = wikidata.read(iris, scholarly=scholarly)
    return [
        {"iri": iri, "label": wikidata.first(getattr(records.get(iri), "label", None)), "description": wikidata.first(getattr(records.get(iri), "description", None))}
        for iri in iris
    ]


def search_iris(term, limit, *, complete=False):
    query = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": term, "srlimit": limit, "format": "json"})
    response = get("https://www.wikidata.org/w/api.php?" + query)
    if response is None or "error" in response or (complete and "continue" in response):
        raise ValueError(f"Incomplete Wikidata search: {term}")
    return ["http://www.wikidata.org/entity/" + row["title"] for row in response.get("query", {}).get("search", [])]


def wikidata_search(term: str) -> list[dict]:
    """The items that the full-text search of Wikidata finds, with their labels."""
    with _wikidata_lock:
        return candidates(search_iris(term, LIMIT))


def by_identifier(prop: str, identifiers: list[str], *, scholarly: bool = False) -> dict[str, list[dict]]:
    """The items whose property (P496, for example) has each identifier (a registered CURIE or an
    IRI), found by rdfsolve."""
    with _wikidata_lock:
        found = wikidata.items(identifiers, prop, scholarly=scholarly)
        return {identifier: candidates(iris, scholarly=scholarly) for identifier, iris in found.items() if iris}


def by_statement(prop: str, values: list[str]) -> dict[str, list[dict]]:
    """The items with a statement of the property with each value (for values of no registered
    identifier scheme), found by the statement search of Wikidata."""
    with _wikidata_lock:
        found = {value: search_iris("haswbstatement:" + json.dumps(f"{prop}={value}"), LIMIT) for value in values}
        return {value: candidates(iris) for value, iris in found.items() if iris}


def ols(term: str, ontologies: str) -> list[dict]:
    return [
        {"iri": t["iri"], "label": t["label"], "description": (t["description"] or [None])[0], "source": t["ontology"]}
        for t in OLS.search(term, ontology=ontologies, exact=False)[:LIMIT]
    ]


def esco(term: str) -> list[dict]:
    data = get("https://ec.europa.eu/esco/api/search?" + urllib.parse.urlencode({"text": term, "type": "occupation", "language": "en", "limit": LIMIT}))
    return [{"iri": r["uri"], "label": r.get("title")} for r in (data or {}).get("_embedded", {}).get("results", [])]


def ror(term: str) -> list[dict]:
    data = get("https://api.ror.org/v2/organizations?" + urllib.parse.urlencode({"query": term}))
    rows = []
    for item in (data or {}).get("items", [])[:LIMIT]:
        name = next((n["value"] for n in item["names"] if "ror_display" in n["types"]), None)
        place = (item.get("locations") or [{}])[0].get("geonames_details", {})
        rows.append({"iri": item["id"], "label": name, "description": f"{place.get('name')}, {place.get('country_name')}"})
    return rows


def iso639(code: str) -> list[dict]:
    iri = f"http://id.loc.gov/vocabulary/iso639-1/{code}"
    return [{"iri": iri, "label": code}] if get(iri + ".json") is not None else []


def issn(doi: str) -> list[str]:
    data = get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    return (data or {}).get("message", {}).get("ISSN", [])


ONTOLOGIES = {"skill": "edam,swo", "taxon": "ncbitaxon", "field": "edam", "software": "swo"}


def search(job: tuple[str, str]) -> dict:
    kind, term = job
    if kind == "language":
        name, code = term.split("|", 1)
        return {"iso639-1": iso639(code), "wikidata": wikidata_search(name + " language")}
    found = {"wikidata": wikidata_search(term)}
    if kind == "organization":
        found["ror"] = ror(term)
    elif kind == "occupation":
        found["esco"] = esco(term)
    if kind in ONTOLOGIES:
        found["ols"] = ols(term, ONTOLOGIES[kind])
    return found


def data(name: str) -> Any:
    return yaml.safe_load((ROOT / "_data" / f"{name}.yml").read_text(encoding="utf-8"))


def inventory() -> dict[str, list[str]]:
    """Search terms are taken from the configured content."""
    cv, events, publications = data("cv"), data("events"), data("publications")
    places = {o["location"] for o in cv["organizations"].values() if o.get("location")}
    places |= {e["location"] for e in events if e.get("location") and e["location"] != "Online"}
    places.add(f"{cv['person']['address']['locality']}, {cv['person']['address']['country']}")
    return {
        "occupation": sorted({job["role"] for job in cv["experience"]}),
        "organization": sorted({o["name"] for o in cv["organizations"].values()}),
        "place": sorted(p.split(",")[0] for p in places),
        "language": [f"{lang['name']}|{lang['code']}" for lang in cv["languages"]],
        "skill": sorted(cv["skill_terms"]),
        "taxon": sorted({t for e in cv["education"] for t in e.get("taxa", [])}),
        "event": sorted({e["name"] for e in events}),
        "project": sorted({p["name"] for p in cv["funded_projects"] + cv["communities"]}),
        "journal": sorted({p["journal"] for p in publications if p.get("journal")}),
        "degree": sorted({study["degree"] for study in cv["education"]}),
        "field": sorted({study.get("field_name", study["field"]) for study in cv["education"]}),
        "software": sorted({s["name"] for s in cv["libraries"] + cv["contributions"] + cv["personal_tools"]}),
    }


def exact_matches() -> dict:
    """Existing identifiers are matched against Wikidata."""
    cv, publications = data("cv"), data("publications")
    rors = [o["iri"].rsplit("/", 1)[1] for o in cv["organizations"].values() if o["iri"].startswith("https://ror.org/")]
    repositories = [s["repository"] for s in cv["libraries"] + cv["contributions"] + cv["personal_tools"] if s.get("repository")]
    dois = [p["doi"].upper() for p in publications if p.get("doi")]
    journals: dict[str, set[str]] = {}
    for pub in publications:
        if pub.get("journal") and pub.get("doi"):
            journals.setdefault(pub["journal"], set()).update(issn(pub["doi"]))
    by_issn = by_identifier("P236", sorted({f"issn:{i}" for values in journals.values() for i in values}))
    taxa = {}
    for term in sorted({t for e in cv["education"] for t in e.get("taxa", [])}):
        match = next((d for d in OLS.search(term, ontology="ncbitaxon")), None)
        if match:
            taxa[term] = re.sub(r".*NCBITaxon_", "", match["iri"])
    return {
        "person (ORCID, P496)": by_identifier("P496", [f"orcid:{cv['person']['orcid']}"]),
        "organizations (ROR, P6782)": by_identifier("P6782", [f"ror:{r}" for r in rors]),
        "languages (ISO 639-1, P218)": by_statement("P218", [lang["code"] for lang in cv["languages"]]),
        "publications (DOI, P356)": by_identifier("P356", [f"doi:{d}" for d in dois], scholarly=True),
        "journals (ISSN, P236)": {j: [row for i in sorted(v) for row in by_issn.get(f"issn:{i}", [])] for j, v in journals.items()},
        "taxa (NCBI Taxonomy, P685)": {
            **by_identifier("P685", [f"ncbitaxon:{t}" for t in taxa.values()]),
            **{f"{k} (NCBITaxon)": [{"iri": f"http://purl.obolibrary.org/obo/NCBITaxon_{v}"}] for k, v in taxa.items()},
        },
        "software (repository, P1324)": by_identifier("P1324", repositories),
    }


def main() -> None:
    jobs = [(kind, term) for kind, terms in inventory().items() for term in terms]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(search, jobs))
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
