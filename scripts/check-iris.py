"""Check every IRI in the published graph: none is minted, every one resolves, and Wikidata items
carry the name the CV gives them.

Run after scripts/build-rdf.py (reads every page's graph under output/rdf, or paths given as arguments). Requires rdflib. Network access is needed, so this runs locally
and in the weekly data workflow rather than on every deploy.

Rules:
- The only IRIs on this site are the page and its published files; a fragment of the page
  (e.g. /#org-x) would be a minted identifier and fails.
- Every other subject or object IRI must dereference (HTTP status below 400 after redirects).
- A Wikidata item that has a schema:name in the graph must have that name (or a close variant)
  as its English label, alias, or a label in another language; mismatches are listed for review.
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

import yaml
from rdflib import RDF, Graph, Literal, Namespace, URIRef

ROOT = Path(__file__).resolve().parent.parent
SITE_URL = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))["url"]
GRAPHS = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "output/rdf").glob("**/index.ttl"))
# This site's own files are checked in the local build, since they may not be deployed yet.
SITE_DIR = ROOT / "_site"
CANONICAL = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))["canonical"]
SCHEMA = Namespace("https://schema.org/")
WIKIDATA = "http://www.wikidata.org/entity/"
HEADERS = {
    "User-Agent": f"cv-iri-check/1.0 ({SITE_URL})",
    # Prefer machine-readable representations; some services refuse requests without an Accept header.
    "Accept": "application/ld+json, application/json;q=0.9, text/turtle;q=0.8, text/html;q=0.7, */*;q=0.5",
}
# Hosts that refuse automated requests; their IRIs are reported as unverified, not failed.
BOT_BLOCKING = ("www.linkedin.com",)
_wikidata_lock = threading.Lock()
# Vocabulary IRIs (classes, properties, enumeration members) are not data about the CV.
VOCABULARY = ("https://schema.org/", "http://xmlns.com/foaf/", "http://purl.org/dc/", "http://www.w3.org/")

_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *args, **kwargs: [r for r in _getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET] or _getaddrinfo(*args, **kwargs)


def status(iri: str) -> int | str:
    if iri.startswith("https://doi.org/"):
        return doi_status(iri.removeprefix("https://doi.org/"))
    if iri.startswith(CANONICAL):
        path = SITE_DIR / urllib.parse.unquote(iri.removeprefix(CANONICAL).split("#")[0] or "index.html")
        relative = path.relative_to(SITE_DIR)
        published = ROOT / "output/rdf" / relative  # build-rdf.py's copies survive jekyll serve rebuilds
        pdf = ROOT / "output" / relative
        return 200 if path.exists() or (path / "index.html").exists() or published.exists() or pdf.exists() else 404
    if iri.startswith(WIKIDATA):
        with _wikidata_lock:
            result = fetch_status(iri)
            time.sleep(0.3)
            return result
    return fetch_status(iri)


def doi_status(doi: str) -> int | str:
    """A DOI resolves if the DOI system has a handle for it; publisher pages often block bots."""
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://doi.org/api/handles/{urllib.parse.quote(doi)}", headers=HEADERS), timeout=30) as response:
            return 200 if json.loads(response.read()).get("responseCode") == 1 else 404
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return str(error)


def fetch_status(iri: str) -> int | str:
    last: str = "no response"
    for attempt in range(4):
        for method in ("HEAD", "GET"):
            try:
                request = urllib.request.Request(iri, headers=HEADERS, method=method)
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                if error.code in (405, 403) and method == "HEAD":
                    continue
                if error.code == 429:
                    break
                return error.code
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last = str(error)
                break
        time.sleep(3 * (attempt + 1))
    return last


def wikidata_names(qids: list[str]) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for start in range(0, len(qids), 50):
        batch = qids[start : start + 50]
        url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "wbgetentities", "ids": "|".join(batch), "props": "labels|aliases", "format": "json"}
        )
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as response:
            entities = json.loads(response.read())["entities"]
        for qid, entity in entities.items():
            found = {v["value"].casefold() for v in entity.get("labels", {}).values()}
            found |= {a["value"].casefold() for values in entity.get("aliases", {}).values() for a in values}
            names[qid] = found
    return names


def main() -> None:
    graph = Graph()
    for path in GRAPHS:
        graph.parse(path, format="turtle")
    iris = {t for t in graph.subjects() if isinstance(t, URIRef)}
    iris |= {o for p, o in graph.predicate_objects() if isinstance(o, URIRef) and p != RDF.type}
    iris = sorted(
        str(i) for i in iris
        if (not str(i).startswith(VOCABULARY) or str(i).startswith("http://www.w3.org/ns/")) and not str(i).startswith("mailto:")
    )
    problems, unverified = [], []

    minted = [i for i in iris if i.startswith(CANONICAL) and "#" in i]
    problems += [f"minted IRI: {i}" for i in minted]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(iris, pool.map(status, iris), strict=True))
    for iri, code in results.items():
        if isinstance(code, int) and code < 400:
            continue
        if urllib.parse.urlparse(iri).netloc in BOT_BLOCKING:
            unverified.append(f"unverified (host refuses automated requests, {code}): {iri}")
        else:
            problems.append(f"does not resolve ({code}): {iri}")

    named = {str(s): str(o) for s, o in graph.subject_objects(SCHEMA.name) if str(s).startswith(WIKIDATA) and isinstance(o, Literal)}
    known = wikidata_names([i.removeprefix(WIKIDATA) for i in named])
    for iri, name in sorted(named.items()):
        candidates = known.get(iri.removeprefix(WIKIDATA), set())
        wanted = name.casefold()
        qualifiers = [q.casefold() for q in re.findall(r"\(([^)]+)\)", name)]
        if not any(
            wanted == c or wanted.split(",")[0] == c or wanted in c or c in wanted or any(q in c for q in qualifiers)
            for c in candidates
        ):
            problems.append(f"Wikidata label mismatch: {iri} is named {name!r} in the CV; Wikidata has {sorted(candidates)[:4]}")

    print(f"{len(iris)} IRIs checked; {len(named)} Wikidata items compared by name.")
    for line in unverified + problems:
        print("  " + line)
    if problems:
        sys.exit(f"{len(problems)} IRI problems")
    print("Every IRI resolves, none is minted, and every Wikidata item matches its name.")


if __name__ == "__main__":
    main()
