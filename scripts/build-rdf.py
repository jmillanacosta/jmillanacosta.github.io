"""Check that the RDFa of each page and its JSON-LD give the same graph, then publish the graph.

Run after `bundle exec jekyll build` and scripts/build-graph.py. The graphs are compared with
Oxigraph after RDF Dataset Canonicalization (RDFC-1.0). The RDFa is read with pyRdfa.
Each page graph is written as Turtle, N-Triples and RDF/XML next to its index.jsonld.
"""

import sys
from collections import Counter
from pathlib import Path

import pyoxigraph as ox
from pyRdfa import pyRdfa

SITE = Path(sys.argv[1] if len(sys.argv) > 1 else "_site").resolve()
PREFIXES = {
    "schema": "https://schema.org/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}
# Only these vocabularies are compared. pyRdfa also gives statements about the HTML page itself.
COMPARED = tuple(PREFIXES[name] for name in ("schema", "foaf", "dcterms", "rdf"))
DUMPS = {"index.ttl": ox.RdfFormat.TURTLE, "index.nt": ox.RdfFormat.N_TRIPLES, "index.rdf": ox.RdfFormat.RDF_XML}
# The landing page, the CV, and the talks and events page each have a graph.
PAGES = {"": "site", "cv/": "CV", "events/": "events"}


def canonical(triples) -> ox.Dataset:
    dataset = ox.Dataset(ox.Quad(t.subject, t.predicate, t.object) for t in triples)
    dataset.canonicalize(ox.CanonicalizationAlgorithm.RDFC_1_0)
    return dataset


def rdfa(page: Path) -> list[ox.Quad]:
    """The statements in the RDFa of the page, in the compared vocabularies."""
    text = pyRdfa().graph_from_source(str(page)).serialize(format="nt")
    return [t for t in ox.parse(text, format=ox.RdfFormat.N_TRIPLES) if t.predicate.value.startswith(COMPARED)]


def page_graph(folder: str) -> list[ox.Quad]:
    """The JSON-LD graph of the page. The script stops if the RDFa gives a different graph."""
    jsonld = list(ox.parse(path=SITE / folder / "index.jsonld", format=ox.RdfFormat.JSON_LD))
    from_page = rdfa(SITE / folder / "index.html")
    if set(canonical(jsonld)) != set(canonical(from_page)):
        # One changed value renames many blank nodes, so the difference is shown without them.
        in_jsonld, in_rdfa = outline(jsonld), outline(from_page)
        for label, extra in (("only in JSON-LD", in_jsonld - in_rdfa), ("only in RDFa", in_rdfa - in_jsonld)):
            for statement in sorted(extra.elements()):
                print(f"{label}: {statement}")
        sys.exit(f"/{folder}: RDFa and JSON-LD differ")
    return jsonld


def outline(triples) -> Counter:
    """The statements with each blank node shown as []."""
    return Counter(
        " ".join("[]" if isinstance(term, ox.BlankNode) else str(term) for term in (t.subject, t.predicate, t.object))
        for t in triples
    )


def publish(name: str, data: bytes) -> None:
    for destination in (SITE / name, Path("output/rdf") / name):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def main() -> None:
    for folder, label in PAGES.items():
        triples = page_graph(folder)
        for name, rdf_format in DUMPS.items():
            publish(folder + name, ox.serialize(triples, format=rdf_format, prefixes=PREFIXES))
        people = {t.subject for t in triples if t.predicate.value == PREFIXES["rdf"] + "type" and t.object.value == PREFIXES["schema"] + "Person"}
        subjects = {t.subject for t in triples}
        print(f"/{folder} ({label}): RDFa and JSON-LD match: {len(triples)} triples, {len(subjects)} resources, {len(people)} people.")
    print(f"Wrote Turtle, N-Triples, and RDF/XML for each page to {SITE}")


if __name__ == "__main__":
    main()
