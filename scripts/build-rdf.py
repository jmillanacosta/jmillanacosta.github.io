"""Check that the page's RDFa and the published JSON-LD describe the same graph, then publish it.

Run after `bundle exec jekyll build`. Requires rdflib, pyrdfa3, and html5lib.
Writes the graph as Turtle, N-Triples, and RDF/XML next to cv.jsonld, and a VoID description at
/.well-known/void and /void.ttl.
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
DUMPS = {"cv.ttl": ("turtle", "Turtle"), "cv.nt": ("nt", "N-Triples"), "cv.rdf": ("xml", "RDF_XML"), "cv.jsonld": (None, "JSON-LD")}


def in_scope(graph: Graph) -> Graph:
    scoped = Graph()
    for triple in graph:
        if str(triple[1]).startswith(VOCABULARIES):
            scoped.add(triple)
    return scoped


def main() -> None:
    jsonld = Graph().parse(SITE / "cv.jsonld", format="json-ld")
    rdfa = in_scope(pyRdfa().graph_from_source(str(SITE / "index.html")))

    _, only_jsonld, only_rdfa = graph_diff(to_isomorphic(jsonld), to_isomorphic(rdfa))
    if len(only_jsonld) or len(only_rdfa):
        for label, graph in (("only in JSON-LD", only_jsonld), ("only in RDFa", only_rdfa)):
            for s, p, o in sorted(graph):
                print(f"{label}: {s.n3()} {p.n3()} {o.n3()}")
        sys.exit(f"RDFa and JSON-LD differ: {len(only_jsonld)} triples only in JSON-LD, {len(only_rdfa)} only in RDFa")

    for prefix, namespace in (("schema", SCHEMA), ("foaf", FOAF), ("dcterms", DCTERMS)):
        jsonld.bind(prefix, namespace)
    for name, (fmt, _) in DUMPS.items():
        if fmt:
            publish(name, jsonld.serialize(format=fmt))
    void_description = void(jsonld)
    publish(".well-known/void", void_description)
    publish("void.ttl", void_description)
    people = len(set(jsonld.subjects(RDF.type, SCHEMA.Person)))
    print(f"RDFa and JSON-LD match: {len(jsonld)} triples, {len(set(jsonld.subjects()))} resources, {people} person.")
    print(f"Wrote {', '.join(name for name, (fmt, _) in DUMPS.items() if fmt)}, and the VoID description to {SITE}")


def publish(name: str, text: str) -> None:
    for destination in (SITE / name, Path("output/rdf") / name):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")


def void(graph: Graph) -> str:
    """VoID and DCAT description of the published graph, with its statistics and downloads."""
    page = next(graph.subjects(RDF.type, SCHEMA.ProfilePage))
    base = str(page)
    dataset = URIRef(base + ".well-known/void#dataset")
    description = Graph()
    for prefix, namespace in (("void", VOID), ("dcat", DCAT), ("dcterms", DCTERMS), ("foaf", FOAF), ("schema", SCHEMA)):
        description.bind(prefix, namespace)
    add = description.add
    add((dataset, RDF.type, VOID.Dataset))
    add((dataset, RDF.type, DCAT.Dataset))
    add((dataset, DCTERMS.title, Literal(str(graph.value(page, SCHEMA.name)), lang="en")))
    add((dataset, DCTERMS.description, Literal("The curriculum vitae of the page's main entity as Linked Data, rendered from the same source as the HTML (with RDFa) and the PDF.", lang="en")))
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
    return description.serialize(format="turtle")


if __name__ == "__main__":
    main()
