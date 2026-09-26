"""Published identifiers are checked for working links and matching Wikidata names."""

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

import pyoxigraph as ox
import yaml
from cvdata import wikidata
from cvdata import net  # Shared HTTP settings.

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))
CANONICAL = CONFIG["canonical"]
GRAPHS = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "output/rdf").glob("**/index.ttl"))
# The files of this site are checked in the local build, because they can be not deployed yet.
SITE_DIR = ROOT / "_site"
AGENT = f"cv-iri-check/1.0 ({CONFIG['url']})"
HEADERS = {
    "User-Agent": AGENT,
    # Machine-readable forms first. Some services refuse requests without an Accept header.
    "Accept": "application/ld+json, application/json;q=0.9, text/turtle;q=0.8, text/html;q=0.7, */*;q=0.5",
}
WIKIDATA = "http://www.wikidata.org/entity/"
NAME = "https://schema.org/name"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
# These hosts refuse automated requests. Their IRIs are reported as not verified, not as failed.
BOT_BLOCKING = ("www.linkedin.com",)
# Vocabulary IRIs (classes, properties, enumeration members) are not data about the CV.
VOCABULARY = ("https://schema.org/", "http://xmlns.com/foaf/", "http://purl.org/dc/", "http://www.w3.org/")
_wikidata_lock = threading.Lock()



def status(iri: str) -> int | str:
    if iri.startswith("https://doi.org/"):
        return doi_status(iri.removeprefix("https://doi.org/"))
    if iri.startswith(CANONICAL):
        path = SITE_DIR / urllib.parse.unquote(iri.removeprefix(CANONICAL).split("#")[0] or "index.html")
        relative = path.relative_to(SITE_DIR)
        published = ROOT / "output/rdf" / relative  # The copies of build-rdf.py stay after jekyll serve rebuilds.
        pdf = ROOT / "output" / relative
        return 200 if path.exists() or (path / "index.html").exists() or published.exists() or pdf.exists() else 404
    if iri.startswith(WIKIDATA):
        with _wikidata_lock:  # Wikidata asks for one request at a time.
            result = fetch_status(iri)
            time.sleep(0.3)
            return result
    return fetch_status(iri)


def doi_status(doi: str) -> int | str:
    """A DOI resolves if the DOI system has a handle for it. Publisher pages often block bots."""
    request = urllib.request.Request(f"https://doi.org/api/handles/{urllib.parse.quote(doi)}", headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return 200 if json.loads(response.read()).get("responseCode") == 1 else 404
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return str(error)


def fetch_status(iri: str) -> int | str:
    last = "no response"
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


def wikidata_names(items: list[str]) -> dict[str, set[str]]:
    """The labels and aliases of Wikidata items, in all languages, in lower case."""
    records = wikidata.read(items, languages=())
    return {
        item: {str(name).casefold() for name in (*getattr(records.get(item), "label", []), *getattr(records.get(item), "alt_label", []))}
        for item in items
    }


def matches(name: str, known: set[str]) -> bool:
    """True if a label or an alias is the name, a part of it, or the qualifier in its brackets."""
    wanted = name.casefold()
    qualifiers = [q.casefold() for q in re.findall(r"\(([^)]+)\)", name)]
    return any(
        wanted == c or wanted.split(",")[0] == c or wanted in c or c in wanted or any(q in c for q in qualifiers)
        for c in known
    )


def main() -> None:
    statements = [t for path in GRAPHS for t in ox.parse(path=path)]
    iris = {t.subject.value for t in statements if isinstance(t.subject, ox.NamedNode)}
    iris |= {t.object.value for t in statements if isinstance(t.object, ox.NamedNode) and t.predicate.value != RDF_TYPE}
    iris = sorted(
        i for i in iris
        if (not i.startswith(VOCABULARY) or i.startswith("http://www.w3.org/ns/")) and not i.startswith("mailto:")
    )
    problems, unverified = [], []
    problems += [f"minted IRI: {i}" for i in iris if i.startswith(CANONICAL) and "#" in i]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(iris, pool.map(status, iris), strict=True))
    for iri, code in results.items():
        if isinstance(code, int) and code < 400:
            continue
        if urllib.parse.urlparse(iri).netloc in BOT_BLOCKING:
            unverified.append(f"not verified (the host refuses automated requests, {code}): {iri}")
        else:
            problems.append(f"does not resolve ({code}): {iri}")

    named = {
        t.subject.value: t.object.value
        for t in statements
        if t.predicate.value == NAME and t.subject.value.startswith(WIKIDATA) and isinstance(t.object, ox.Literal)
    }
    known = wikidata_names(sorted(named))
    for iri, name in sorted(named.items()):
        if not matches(name, known[iri]):
            problems.append(f"Wikidata label mismatch: {iri} is named {name!r} in the CV; Wikidata has {sorted(known[iri])[:4]}")

    print(f"{len(iris)} IRIs checked; {len(named)} Wikidata items compared by name.")
    for line in unverified + problems:
        print("  " + line)
    if problems:
        sys.exit(f"{len(problems)} IRI problems")
    print("Every IRI resolves, no IRI is minted, and every Wikidata item has its name.")


if __name__ == "__main__":
    main()
