"""Wikidata is read through rdfsolve clients built from schemas that rdfsolve mined.

scripts/mine-wikidata.py mines the schemas around the Wikidata items of the site data: one for the
main graph of the Wikidata Query Service (people, events, properties) and one for the graph of
scholarly works. The typed records have the fields that these items have, named after the English
labels of the properties: label, start_time, organizer, orcid_id. FIELDS lists the fields that
the site reads, so that a new mining run shows when Wikidata no longer gives one of them.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from typing import Any

from rdflib import RDF, XSD, Literal, Namespace, URIRef
from rdfsolve import MinedSchema
from rdfsolve.api import Client
from rdfsolve.sparql_helper import EndpointError

from .config import ROOT

WD = "http://www.wikidata.org/entity/"
P = "http://www.wikidata.org/prop/"
WDT = Namespace("http://www.wikidata.org/prop/direct/")
WIKIBASE = Namespace("http://wikiba.se/ontology#")
ENDPOINTS = {False: "https://query.wikidata.org/sparql", True: "https://query-scholarly.wikidata.org/sparql"}
SCHEMAS = {False: ROOT / "schema/wikidata.schema.json", True: ROOT / "schema/wikidata-scholarly.schema.json"}
# The fields that the site reads, by model, in each graph.
WORK = {"label", "main_subject", "author_statement"}
FIELDS = {
    False: {
        "Item": WORK | {"description", "instance_of", "start_time", "end_time", "official_website", "part_of_the_series", "organizer", "orcid_id", "github_account", "pypi_project", "npm_package", "github_topic", "equivalent_class", "exact_match"},
        "Property": {"label", "property_type", "direct_claim", "subproperty_of", "equivalent_property", "formatter_url"},
        "Statement": {"author", "series_ordinal"},
    },
    True: {"Item": WORK, "Statement": {"author", "series_ordinal"}},
}


@cache
def client(scholarly: bool = False) -> Client:
    """The client of the main graph, or of the graph of scholarly works."""
    return Client(MinedSchema.from_json(SCHEMAS[scholarly]), ENDPOINTS[scholarly], timeout=45, max_rows=10000)


def missing(schema: MinedSchema, scholarly: bool = False) -> dict[str, set[str]]:
    """The fields that the site reads and that the models of the schema do not have, by model."""
    models = {name: set(model.model_fields) for name, model in Client(schema).models.items()}
    return {name: gone for name, fields in FIELDS[scholarly].items() if (gone := fields - models.get(name, set()))}


def read(iris: Iterable[str], kind: str = "Item", *, scholarly: bool = False, languages=("en",)) -> dict[str, Any]:
    """The records of these IRIs (Item, Property or Statement), by IRI. Names are in the languages
    (all languages when empty); IRIs that Wikidata no longer has are left out."""
    source = client(scholarly)
    iris = sorted({str(iri) for iri in iris})
    found = {}
    for start in range(0, len(iris), source.max_subjects):
        batch = iris[start : start + source.max_subjects]
        for record in source.get_many(source.model(kind), batch, languages=languages, missing="skip"):
            found[str(record.uri)] = record
    return found


def first(values: Iterable[Any] | None) -> str | None:
    """The first value in sorted order, as text."""
    values = sorted(str(value) for value in values or [])
    return values[0] if values else None


def items(identifiers: Iterable[str], prop: str | None = None, *, scholarly: bool = False) -> dict[str, list[str]]:
    """The items that carry each identifier (a registered CURIE such as doi:10.1/x, or an IRI),
    as the value of the property (P356, for example) when one is given."""
    found: dict[str, set[str]] = {identifier: set() for identifier in identifiers}
    try:
        for match in client(scholarly).identify(list(found)):
            if prop is None or match.predicate == str(WDT[prop]):
                found[match.identifier].add(match.resource)
    except EndpointError:
        return {}
    return {identifier: sorted(iris) for identifier, iris in found.items()}


def item(identifier: str, prop: str | None = None, *, scholarly: bool = False) -> str | None:
    """The QID of the one item that carries an identifier; None when there is not exactly one."""
    found = items([identifier], prop, scholarly=scholarly).get(identifier, [])
    return found[0].removeprefix(WD) if len(found) == 1 else None


def works(dois: Iterable[str]) -> dict[str, tuple[str, bool]]:
    """The item of each work by DOI (in upper case), and whether it is in the scholarly graph.
    Journal articles are in the scholarly graph; other works, such as preprints, in the main one."""
    wanted, found = sorted({doi.upper() for doi in dois}), {}
    for scholarly in (False, True):
        matches = items([f"doi:{doi}" for doi in wanted if doi not in found], "P356", scholarly=scholarly)
        found.update({key.removeprefix("doi:"): (iris[0], scholarly) for key, iris in matches.items() if len(iris) == 1})
    return found


def names(iris: Iterable[str]) -> list[dict[str, str]]:
    """The IRI and English name of each item."""
    iris = list(iris)
    records = read(iris)
    return [{"iri": iri, "name": first(getattr(records.get(iri), "label", None)) or iri} for iri in sorted(set(iris))]


def schema_org(values: Iterable[str]) -> list[str]:
    """The schema.org IRIs among the values, written with https."""
    iris = {str(v).replace("http://schema.org/", "https://schema.org/") for v in values}
    return sorted(iri for iri in iris if iri.startswith("https://schema.org/"))


def properties(direct_claims: Iterable[URIRef]) -> dict[URIRef, Any]:
    """The property records of direct claims (wdt:P...), by direct claim."""
    wanted = [WD + str(p).removeprefix(str(WDT)) for p in direct_claims if str(p).startswith(str(WDT))]
    records = read(wanted, "Property")
    return {URIRef(claim): record for record in records.values() for claim in record.direct_claim}


def facts(qids: list[str]) -> list[dict[str, Any]]:
    """For each item: its IRI, English label, ORCID, GitHub account, external identifiers, and
    official websites (one row for each website, or one row without)."""
    iris = [WD + qid for qid in qids]
    graph = client().statements(iris, languages=["en"])
    identifiers = {
        claim: str(record.uri).removeprefix(WD)
        for claim, record in properties(set(graph.predicates())).items()
        if str(WIKIBASE.ExternalId) in record.property_type
    }
    rows = []
    for iri, record in read(iris).items():
        pairs = {(identifiers[p], str(v)) for p, v in graph.predicate_objects(URIRef(iri)) if p in identifiers}
        ids = sorted(pairs, key=lambda pair: (int(pair[0][1:]), pair[1]))
        base: dict[str, Any] = {"item": iri, "ids": [list(pair) for pair in ids]}
        for key, value in (("label", first(record.label)), ("orcid", first(record.orcid_id)), ("gh", first(record.github_account))):
            if value:
                base[key] = value
        rows += [{**base, "site": site} for site in sorted(record.official_website)] or [base]
    return rows


def claims(qids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """The claims of items that are not identifiers, with the schema.org terms that Wikidata gives
    for their properties (P1628, also of parent properties), values and value classes (P1709, P2888)."""
    iris = [WD + qid for qid in qids]
    graph = client().statements(iris, languages=["en"])
    direct = {claim: record for claim, record in properties(set(graph.predicates())).items() if str(WIKIBASE.ExternalId) not in record.property_type}
    known = {str(record.uri): record for record in direct.values()}
    missing = {parent for record in known.values() for parent in record.subproperty_of} - known.keys()
    while missing:  # Parent properties, up to the root.
        found = read(missing, "Property")
        known.update(found)
        missing = {parent for record in found.values() for parent in record.subproperty_of} - known.keys()

    def equivalent(prop: str, seen: frozenset = frozenset()) -> list[str]:
        """The schema.org properties of a property and of its parents."""
        record = known.get(prop)
        if record is None or prop in seen:
            return []
        own = schema_org(record.equivalent_property)
        return sorted({*own, *(e for parent in record.subproperty_of for e in equivalent(parent, seen | {prop}))})

    values = read(v for _, p, v in graph if p in direct and str(v).startswith(WD))
    classes = read(c for record in values.values() for c in record.instance_of)
    found = {}
    for qid, iri in zip(qids, iris, strict=True):
        rows = []
        for claim, value in graph.predicate_objects(URIRef(iri)):
            record = direct.get(claim)
            if record is None:
                continue
            prop = str(record.uri)
            row = {"property": prop, "label": first(record.label) or prop, "equivalent": equivalent(prop), "value": str(value)}
            if isinstance(value, Literal):
                if value.language:
                    row["language"] = value.language
                row["datatype"] = str(value.datatype or (RDF.langString if value.language else XSD.string))
            elif str(value) in values:
                target = values[str(value)]
                row["value"] = (schema_org([*target.equivalent_class, *target.exact_match]) or [str(value)])[0]
                if target.label:
                    row["value_label"] = first(target.label)
                types = schema_org(t for c in target.instance_of if c in classes for t in [*classes[c].equivalent_class, *classes[c].exact_match])
                if types:
                    row["value_type"] = types[0]
            rows.append(row)
        found[qid] = sorted(rows, key=lambda row: (row["property"], row["value"]))
    return found


def formatters(props: list[str]) -> dict[str, dict[str, str]]:
    """For identifier properties: the English label and the URL pattern (P1630) of a link."""
    records = read((WD + prop for prop in props), "Property")
    return {
        prop: {"label": first(record.label), "pattern": first(record.formatter_url)}
        for prop, record in ((prop, records.get(WD + prop)) for prop in props)
        if record is not None and record.label and record.formatter_url
    }
