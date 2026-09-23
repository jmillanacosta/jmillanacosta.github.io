"""Check that the page's RDFa and the published JSON-LD describe the same graph, then publish it.

Run after `bundle exec jekyll build`. Requires rdflib, pyrdfa3, and html5lib.
Writes each page's graph as Turtle, N-Triples, and RDF/XML next to its index.jsonld, and a VoID
description of all three at /.well-known/void and /void.ttl.
"""

import sys
from pathlib import Path

from pyRdfa import pyRdfa
from rdflib import RDF, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.compare import graph_diff, to_isomorphic

SITE = Path(sys.argv[1] if len(sys.argv) > 1 else "_site")
SCHEMA = Namespace("https://schema.org/")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
DCTERMS = Namespace("http://purl.org/dc/terms/")
VOID = Namespace("http://rdfs.org/ns/void#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
FORMATS = Namespace("http://www.w3.org/ns/formats/")
VOCABULARIES = (str(SCHEMA), str(FOAF), str(DCTERMS), str(RDF))
DUMPS = {"index.ttl": ("turtle", "Turtle"), "index.nt": ("nt", "N-Triples"), "index.rdf": ("xml", "RDF_XML"), "index.jsonld": (None, "JSON-LD")}
# Each page publishes its own graph next to it: the landing page, the CV, and talks and events.
PAGES = {"": "site", "cv/": "CV", "events/": "events"}


def in_scope(graph: Graph) -> Graph:
    scoped = Graph()
    for triple in graph:
        if str(triple[1]).startswith(VOCABULARIES):
            scoped.add(triple)
    return scoped


def page_graph(folder: str) -> Graph:
    """The page's JSON-LD graph, after checking that its RDFa states exactly the same triples."""
    jsonld = Graph().parse(SITE / folder / "index.jsonld", format="json-ld")
    rdfa = in_scope(pyRdfa().graph_from_source(str(SITE / folder / "index.html")))
    _, only_jsonld, only_rdfa = graph_diff(to_isomorphic(jsonld), to_isomorphic(rdfa))
    if len(only_jsonld) or len(only_rdfa):
        for label, graph in (("only in JSON-LD", only_jsonld), ("only in RDFa", only_rdfa)):
            for s, p, o in sorted(graph):
                print(f"{label}: {s.n3()} {p.n3()} {o.n3()}")
        sys.exit(f"/{folder}: RDFa and JSON-LD differ: {len(only_jsonld)} triples only in JSON-LD, {len(only_rdfa)} only in RDFa")
    for prefix, namespace in (("schema", SCHEMA), ("foaf", FOAF), ("dcterms", DCTERMS)):
        jsonld.bind(prefix, namespace)
    return jsonld


def main() -> None:
    graphs = {}
    for folder, label in PAGES.items():
        graph = graphs[folder] = page_graph(folder)
        for name, (fmt, _) in DUMPS.items():
            if fmt:
                publish(folder + name, graph.serialize(format=fmt))
        people = len(set(graph.subjects(RDF.type, SCHEMA.Person)))
        print(f"/{folder} ({label}): RDFa and JSON-LD match: {len(graph)} triples, {len(set(graph.subjects()))} resources, {people} people.")
    void_description = void(graphs)
    publish(".well-known/void", void_description)
    publish("void.ttl", void_description)
    print(f"Wrote Turtle, N-Triples, and RDF/XML for each page, and the VoID description, to {SITE}")


def publish(name: str, text: str) -> None:
    for destination in (SITE / name, Path("output/rdf") / name):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")


def void(graphs: dict[str, Graph]) -> str:
    """VoID and DCAT description of each page's graph."""
    description = Graph()
    for prefix, namespace in (("void", VOID), ("dcat", DCAT), ("dcterms", DCTERMS), ("foaf", FOAF), ("schema", SCHEMA)):
        description.bind(prefix, namespace)
    for folder, graph in graphs.items():
        describe(description, folder, graph)
    return description.serialize(format="turtle")


def describe(description: Graph, folder: str, graph: Graph) -> URIRef:
    add = description.add
    page = next(graph.subjects(RDF.type, SCHEMA.ProfilePage))
    base = str(page)
    dataset = URIRef(base.removesuffix(folder) + ".well-known/void#" + (PAGES[folder].lower()))
    add((dataset, RDF.type, VOID.Dataset))
    add((dataset, RDF.type, DCAT.Dataset))
    add((dataset, DCTERMS.title, Literal(str(graph.value(page, SCHEMA.name)), lang="en")))
    add((dataset, DCTERMS.description, Literal(f"The {PAGES[folder]} graph, rendered from the same source as the page's RDFa.", lang="en")))
    person = graph.value(page, SCHEMA.mainEntity)
    if person is None:
        sys.exit("The graph has no schema:mainEntity for the page")
    add((dataset, DCTERMS.creator, person))
    add((dataset, FOAF.primaryTopic, person))
    modified = graph.value(page, SCHEMA.dateModified)
    if modified is not None:
        add((dataset, DCTERMS.modified, modified))
    add((dataset, FOAF.homepage, page))
    add((dataset, VOID.rootResource, page))
    for name, (_, feature) in DUMPS.items():
        add((dataset, VOID.dataDump, URIRef(base + name)))
        add((dataset, VOID.feature, FORMATS[feature]))
    for namespace in (SCHEMA, FOAF, DCTERMS):
        add((dataset, VOID.vocabulary, URIRef(str(namespace))))
    classes = sorted(set(graph.objects(None, RDF.type)), key=str)
    properties = sorted(set(graph.predicates()), key=str)
    add((dataset, VOID.triples, Literal(len(graph), datatype=XSD.integer)))
    add((dataset, VOID.distinctSubjects, Literal(len(set(graph.subjects())), datatype=XSD.integer)))
    add((dataset, VOID.distinctObjects, Literal(len(set(graph.objects())), datatype=XSD.integer)))
    add((dataset, VOID.classes, Literal(len(classes), datatype=XSD.integer)))
    add((dataset, VOID.properties, Literal(len(properties), datatype=XSD.integer)))
    for cls in classes:
        partition = BNode()
        add((dataset, VOID.classPartition, partition))
        add((partition, VOID["class"], cls))
        add((partition, VOID.entities, Literal(len(set(graph.subjects(RDF.type, cls))), datatype=XSD.integer)))
    for prop in properties:
        partition = BNode()
        add((dataset, VOID.propertyPartition, partition))
        add((partition, VOID.property, prop))
        add((partition, VOID.triples, Literal(len(list(graph.triples((None, prop, None)))), datatype=XSD.integer)))
    return dataset


if __name__ == "__main__":
    main()
