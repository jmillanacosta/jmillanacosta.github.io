"""The site schema and VoID description are mined and exported with rdfsolve."""

import html
import json
import os
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import pyoxigraph as ox
import yaml
from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, FOAF, VOID
from rdfsolve import MinedSchema, SchemaMiner
from rdfsolve.api import Client

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site"
CONCEPTS: dict[str, Any] = yaml.safe_load((ROOT / "_data/concepts.yml").read_text(encoding="utf-8"))
CONFIG: dict[str, Any] = CONCEPTS["schema"]
FORMATS = Namespace("http://www.w3.org/ns/formats/")
V = CONCEPTS["vocabulary"]


def graphs() -> dict[str, ox.Dataset]:
    """The graph of each source file."""
    return {source: ox.Dataset(ox.parse(path=SITE / source)) for source in CONCEPTS["sources"]}


def mine(data: ox.Dataset) -> MinedSchema:
    with SchemaMiner.from_graph(data, delay=0) as miner:
        return miner.mine(dataset_name=CONFIG["dataset"])


def value(data: ox.Dataset, subject: Any, predicate: str) -> Any:
    """The first value of *predicate* for *subject*, or None."""
    return next((q.object for q in data.quads_for_subject(subject) if q.predicate.value == predicate), None)


def statistics(description: Graph, dataset: URIRef, data: ox.Dataset) -> None:
    for predicate, count in (
        (VOID.triples, len(data)),
        (VOID.distinctSubjects, len({q.subject for q in data})),
        (VOID.distinctObjects, len({q.object for q in data})),
    ):
        description.set((dataset, predicate, Literal(count, datatype=XSD.integer)))


def void(parts: dict[str, ox.Dataset], schema: MinedSchema) -> Graph:
    """The VoID description: the whole site, and one subset for each graph."""
    from rdfsolve.config import mint

    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    everything = ox.Dataset([q for part in parts.values() for q in part])
    description = schema.to_void_graph()
    root = URIRef(mint("dataset", CONFIG["dataset"]))
    description.add((root, RDF.type, DCAT.Dataset))
    description.set((root, DCTERMS.title, Literal(site["name"], lang="en")))
    description.add((root, DCTERMS.description, Literal(CONFIG["void"]["description"], lang="en")))
    description.add((root, FOAF.homepage, URIRef(site["canonical"])))
    statistics(description, root, everything)
    formats = [f for f in yaml.safe_load((ROOT / "_data/formats.yml").read_text(encoding="utf-8")) if f.get("describes")]
    for source, part in parts.items():
        folder, stem = str(Path(source).parent).strip("."), Path(source).stem
        prefix = f"{folder}/" if folder else ""
        subset = URIRef(mint("dataset", CONFIG["dataset"], "-".join(Path(source).with_suffix("").parts)))
        description.add((root, VOID.subset, subset))
        description.add((subset, RDF.type, VOID.Dataset))
        description.add((subset, RDF.type, DCAT.Dataset))
        statistics(description, subset, part)
        files = {prefix + f["url"]: f for f in formats if Path(f["url"]).stem == stem}
        files.setdefault(source, next((f for f in formats if Path(f["url"]).suffix == Path(source).suffix), None))
        for name, fmt in files.items():
            if (SITE / name).exists():
                for dataset in (subset, root):
                    description.add((dataset, VOID.dataDump, URIRef(site["canonical"] + name)))
                if fmt:
                    description.add((subset, VOID.feature, FORMATS[fmt["name"].replace("/", "_")]))
        page_type = ox.NamedNode(V + CONCEPTS["profile"]["type"])
        page = next((q.subject for q in part.quads_for_object(page_type) if q.predicate.value == str(RDF.type)), None)
        if page is not None:
            owner = URIRef(value(part, page, V + CONCEPTS["profile"]["subject"]).value)
            modified = value(part, page, V + CONCEPTS["profile"]["modified"])
            title = value(part, page, V + CONCEPTS["terms"]["name"]).value
            description.add((subset, DCTERMS.title, Literal(title, lang="en")))
            description.add((subset, FOAF.homepage, URIRef(page.value)))
            description.add((subset, VOID.rootResource, URIRef(page.value)))
            for dataset in (subset, root):
                description.add((dataset, DCTERMS.creator, owner))
                description.add((dataset, FOAF.primaryTopic, owner))
                if modified is not None:
                    date = Literal(modified.value, datatype=URIRef(modified.datatype.value))
                    description.set((dataset, DCTERMS.modified, date))
        else:
            description.add((subset, DCTERMS.title, Literal(stem, lang="en")))
    return description


def mermaid(schema: MinedSchema) -> str:
    return Client(schema).diagram(namespaces=["schema"]).strip().removeprefix("```mermaid").removesuffix("```").strip()


def words(iri: str) -> str:
    name = re.split(r"[/#]", iri)[-1]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower().capitalize()


def local(iri: str) -> str:
    return re.split(r"[/#]", iri)[-1]


def links(schema: MinedSchema) -> dict[tuple[str, str], set[str]]:
    found = defaultdict(set)
    for p in schema.patterns:
        found[(p.subject_class, p.object_class)].add(p.property_uri)
    for c in schema.collections or []:
        for member in c.member_types:
            found[(c.subject_class, member)].add(c.property_uri)
    return {pair: props for pair, props in found.items() if all(c.startswith(V) for c in pair)}


def tree(schema: MinedSchema) -> str:
    """The classes as a tree from the root class, in the manner of a UML class diagram: each class
    with its properties and the types of their values. A class opens where it is first reached
    (breadth first); elsewhere its name leads there. Classes that the root does not reach follow."""
    values: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for p in schema.patterns:
        if p.subject_class.startswith(V) and p.property_uri != str(RDF.type):
            values[p.subject_class][p.property_uri].add(p.datatype or p.object_class)
    lists = {(c.subject_class, c.property_uri): set(c.member_types) for c in schema.collections or []}
    folders = {t: c["folder"] for c in CONCEPTS["categories"] for t in c.get("types", [])}
    prefixes = {namespace: prefix for prefix, namespace in schema.get_prefixes().items()}

    def types(cls: str, prop: str) -> list[str]:
        found = lists.get((cls, prop)) or values[cls][prop] - {"BlankNode"} or values[cls][prop]
        return sorted(found, key=lambda v: (v not in values, local(v)))

    def props(cls: str) -> list[str]:  # attributes first, then associations
        return sorted(values[cls], key=lambda p: (any(v in values for v in types(cls, p)), not p.startswith(V), local(p).casefold()))

    roots, parent, queue = [], {}, deque()
    for start in [V + CONFIG["root"], *sorted(values, key=local)]:
        if start in values and start not in parent:
            roots.append(start)
            parent[start] = None
            queue.append(start)
        while queue:
            cls = queue.popleft()
            for prop in props(cls):
                for value in types(cls, prop):
                    if value in values and value not in parent:
                        parent[value] = (cls, prop)
                        queue.append(value)

    def term(iri: str) -> str:
        """The local name; a term of another vocabulary than the site's keeps its prefix."""
        namespace = iri[: -len(local(iri))]
        if iri.startswith(V) or namespace in (str(XSD), str(RDF)) or namespace not in prefixes:
            return CONFIG["value_names"].get(iri, local(iri))
        return f"{prefixes[namespace]}:{local(iri)}"

    def kind(value: str) -> str:
        if value in values:
            return f'<a href="#schema-{local(value)}">{html.escape(local(value))}</a>'
        return html.escape(term(value))

    def box(cls: str, opened: bool = False) -> str:
        rows = []
        for prop in props(cls):
            found = types(cls, prop)
            row = f'<span class="uml-prop">{html.escape(term(prop))}</span>: <span class="uml-type">{" | ".join(kind(v) for v in found)}</span>'
            if (cls, prop) in lists:
                row += ' <span class="uml-type">[*] {ordered}</span>'
            here = [v for v in found if parent.get(v) == (cls, prop)]
            if here:
                row += '<ul class="tree-leaves">' + "".join(f"<li>{box(v)}</li>" for v in here) + "</ul>"
            rows.append(f"<li>{row}</li>")
        name = html.escape(local(cls))
        count = html.escape(CONFIG["properties"][len(rows) != 1].format(count=len(rows)))
        instances = f' <a class="uml-instances" href="/{folders[local(cls)]}/">{html.escape(CONFIG["instances"])}</a>' if local(cls) in folders else ""
        return (
            f'<details class="uml-box" id="schema-{name}"{" open" if opened else ""}><summary><span class="uml-name">{name}</span>'
            f' <span class="uml-type">{count}</span>{instances}</summary><ul class="tree-leaves">{"".join(rows)}</ul></details>'
        )

    rest = "".join(f"<li>{box(cls)}</li>" for cls in roots[1:])
    others = f'<p class="tree-label">{html.escape(CONFIG["unreached"])}</p><ul class="tree-leaves">{rest}</ul>' if rest else ""
    # A link to a class opens the class.
    script = '<script>const openTarget = () => { const box = document.getElementById(decodeURIComponent(location.hash.slice(1))); if (box instanceof HTMLDetailsElement) box.open = true; }; addEventListener("hashchange", openTarget); openTarget();</script>'
    return f'<div class="schema-tree">{box(roots[0], opened=True)}{others}</div>{script}'


def page(shell: str, schema: MinedSchema) -> str:
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    edges = links(schema)
    classes = len({c for pair in edges for c in pair})
    lede = CONFIG["lede"].format(classes=classes, links=len(edges))
    downloads = "".join(
        f'<li><a href="{d["file"]}">{html.escape(d["name"])}</a>'
        + (f' <span class="concept-meta">{html.escape(d["note"])}</span>' if d.get("note") else "")
        + "</li>"
        for d in CONFIG["downloads"]
    )
    body = f"""<div class="cv concept">
<header class="cv-header concept-header"><p class="concept-kind">{html.escape(CONFIG["kicker"])}</p>
<h1>{html.escape(CONFIG["title"])}</h1><p class="concept-description">{html.escape(lede)}</p></header>
<section class="cv-section" aria-labelledby="diagram"><h2 id="diagram">{html.escape(CONFIG["diagram_title"])}</h2>
<p class="schema-hint">{html.escape(CONFIG["hint"])}</p>
{tree(schema)}</section>
<section class="cv-section" aria-labelledby="downloads"><h2 id="downloads">{html.escape(CONFIG["downloads_title"])}</h2>
<ul class="schema-downloads">{downloads}</ul></section>
</div>"""
    head = "\n".join([
        f"<title>{html.escape(CONFIG['title'])} · {html.escape(site['name'])}</title>",
        f'<meta name="description" content="{html.escape(lede)}" />',
        f'<link rel="canonical" href="{html.escape(site["canonical"] + CONFIG["folder"])}/" />',
        f'<link rel="describedby" type="application/json" href="{CONFIG["schema_file"]}" title="rdfsolve schema" />',
    ])
    return shell.replace("<!--concept:head-->", head).replace("<!--concept:body-->", body).replace("<!--concept:footer-->", "")


def main() -> None:
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    os.environ["RDFSOLVE_BASE_URI"] = site["canonical"] + CONFIG["void"]["base"]
    parts = graphs()
    schema = mine(ox.Dataset([q for part in parts.values() for q in part]))
    files = {
        CONFIG["schema_file"]: json.dumps(schema.to_dict(), indent=1) + "\n",
        "models.py": schema.to_pydantic(contract=True),
        "shapes.ttl": schema.to_shacl(activate_observed=True),
        "schema.linkml.yaml": schema.to_linkml_yaml(),
        "diagram.mmd": mermaid(schema),
    }
    folder = SITE / CONFIG["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    shell = (SITE / CONCEPTS["files"]["shell"]).read_text(encoding="utf-8")
    (folder / "index.html").write_text(page(shell, schema), encoding="utf-8")
    print(f"Wrote the schema page: {len(links(schema))} links, to {folder}")
    description = void(parts, schema).serialize(format="turtle")
    for name in CONFIG["void"]["files"]:
        (SITE / name).parent.mkdir(parents=True, exist_ok=True)
        (SITE / name).write_text(description, encoding="utf-8")
    print(f"Wrote the VoID description: {', '.join(CONFIG['void']['files'])}")


if __name__ == "__main__":
    main()
