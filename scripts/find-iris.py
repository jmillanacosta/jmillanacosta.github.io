"""Find candidate IRIs for each concept of the CV. The candidates are written to
output/iri-candidates.yml for review. The chosen IRIs are then put in the data files.

Wikidata is asked with SPARQL through rdfsolve: a text search (the MediaWiki search service of
the Wikidata query service), and exact matches for the identifiers that the CV already has
(ORCID, ROR, ISO 639, DOI, ISSN, NCBI Taxonomy, repositories). Scholarly articles are in the
scholarly query service of Wikidata, the other items in the main one. Ontology terms (EDAM, the Software
Ontology, NCBI Taxonomy) are searched in OLS through rdfsolve. ROR, ESCO, Crossref and the
Library of Congress are asked through their own web APIs.

Usage: python3 scripts/find-iris.py [--limit N]
"""

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
from rdfsolve.sparql_helper import SparqlHelper

ROOT = Path(__file__).resolve().parent.parent
AGENT = f"cv-iri-finder/1.0 ({yaml.safe_load((ROOT / '_config.yml').read_text(encoding='utf-8'))['url']})"
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 5
# Since the graph split of 2025, scholarly articles are in their own Wikidata query service.
WIKIDATA = SparqlHelper("https://query.wikidata.org/sparql", user_agent=AGENT, timeout=60)
SCHOLARLY = SparqlHelper("https://query-scholarly.wikidata.org/sparql", user_agent=AGENT, timeout=60)
OLS = OntologyLookup(max_requests=1000)
# Wikidata asks for one request at a time.
_wikidata_lock = threading.Lock()


def get(url: str, attempts: int = 4) -> Any:
    """Get JSON from a web API. A rate-limited or failed request is tried again, so that it is
    not read as "no results"."""
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


def wikidata_rows(query: str, *, scholarly: bool = False) -> list[dict]:
    with _wikidata_lock:
        return (SCHOLARLY if scholarly else WIKIDATA).select(query)["results"]["bindings"]


def candidate(row: dict) -> dict:
    return {
        "iri": row["item"]["value"],
        "label": row.get("label", {}).get("value"),
        "description": row.get("description", {}).get("value"),
    }


def wikidata(term: str) -> list[dict]:
    """Items found by the full-text search of Wikidata, in the order of the search."""
    rows = wikidata_rows(f"""SELECT ?item ?label ?description WHERE {{
  SERVICE wikibase:mwapi {{
    bd:serviceParam wikibase:api "Search"; wikibase:endpoint "www.wikidata.org";
      mwapi:srsearch {json.dumps(term)}; mwapi:srlimit "{LIMIT}" .
    ?title wikibase:apiOutput mwapi:title . ?rank wikibase:apiOrdinal true .
  }}
  BIND(IRI(CONCAT(STR(wd:), ?title)) AS ?item)
  OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
  OPTIONAL {{ ?item schema:description ?description FILTER(LANG(?description) = "en") }}
}} ORDER BY ?rank""")
    return [candidate(row) for row in rows]


def by_identifier(prop: str, values: list[str], *, scholarly: bool = False) -> dict[str, list[dict]]:
    """Exact Wikidata matches for identifiers that the CV already has."""
    if not values:
        return {}
    listed = " ".join(json.dumps(v) for v in values)
    rows = wikidata_rows(
        f"SELECT ?value ?item ?label ?description WHERE {{ VALUES ?value {{ {listed} }} ?item wdt:{prop} ?value . "
        'OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label) = "en") } '
        'OPTIONAL { ?item schema:description ?description FILTER(LANG(?description) = "en") } }',
        scholarly=scholarly,
    )
    found: dict[str, list[dict]] = {}
    for row in rows:
        found.setdefault(row["value"]["value"], []).append(candidate(row))
    return found


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
# Organizations that are not in the CV yet, with their ROR identifiers.
OTHER_ORGANIZATIONS = {"RECETOX": "032hzqh22", "Masaryk University": "02j46qs45"}
DEGREES = ["Master of Science", "Bachelor of Science", "Doctor of Philosophy"]
FIELDS = ["bioinformatics", "biotechnology"]
OCCUPATIONS = ["doctoral researcher", "PhD candidate", "researcher", "research intern"]


def data(name: str) -> Any:
    return yaml.safe_load((ROOT / "_data" / f"{name}.yml").read_text(encoding="utf-8"))


def inventory() -> dict[str, list[str]]:
    """The terms to search, by kind of concept."""
    cv, events, publications = data("cv"), data("events"), data("publications")
    places = {o["location"] for o in cv["organizations"].values() if o.get("location")}
    places |= {e["location"] for e in events if e.get("location") and e["location"] != "Online"}
    places.add(f"{cv['person']['address']['locality']}, {cv['person']['address']['country']}")
    return {
        "occupation": OCCUPATIONS,
        "organization": sorted({o["name"] for o in cv["organizations"].values()} | set(OTHER_ORGANIZATIONS)),
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


def exact_matches() -> dict:
    """Wikidata items that have an identifier that the CV already gives."""
    cv, publications = data("cv"), data("publications")
    rors = [o["iri"].rsplit("/", 1)[1] for o in cv["organizations"].values() if o["iri"].startswith("https://ror.org/")]
    rors += list(OTHER_ORGANIZATIONS.values())
    repositories = [s["repository"] for s in cv["libraries"] + cv["contributions"] + cv["personal_tools"] if s.get("repository")]
    dois = [p["doi"].upper() for p in publications if p.get("doi")]
    journals: dict[str, set[str]] = {}
    for pub in publications:
        if pub.get("journal") and pub.get("doi"):
            journals.setdefault(pub["journal"], set()).update(issn(pub["doi"]))
    by_issn = by_identifier("P236", sorted({i for values in journals.values() for i in values}))
    taxa = {}
    for term in sorted({t for e in cv["education"] for t in e.get("taxa", [])}):
        match = next((d for d in OLS.search(term, ontology="ncbitaxon")), None)
        if match:
            taxa[term] = re.sub(r".*NCBITaxon_", "", match["iri"])
    return {
        "person (ORCID, P496)": by_identifier("P496", [cv["person"]["orcid"]]),
        "organizations (ROR, P6782)": by_identifier("P6782", rors),
        "languages (ISO 639-1, P218)": by_identifier("P218", [lang["code"] for lang in cv["languages"]]),
        "publications (DOI, P356)": by_identifier("P356", dois, scholarly=True),
        "journals (ISSN, P236)": {j: [row for i in sorted(v) for row in by_issn.get(i, [])] for j, v in journals.items()},
        "taxa (NCBI Taxonomy, P685)": {
            **by_identifier("P685", list(taxa.values())),
            **{f"{k} (NCBITaxon)": [{"iri": f"http://purl.obolibrary.org/obo/NCBITaxon_{v}"}] for k, v in taxa.items()},
        },
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
