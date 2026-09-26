"""The schema page and the VoID description of the site, mined by rdfsolve from the built graphs.

Run after scripts/build-graph.py. The graphs are read with Oxigraph. The schema is mined from all
graphs together; the VoID description has one subset for each graph.
"""

import html
import json
import math
import os
import re
import sys
from collections import defaultdict
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


def links(schema: MinedSchema) -> dict[tuple[str, str], set[str]]:
    found = defaultdict(set)
    for p in schema.patterns:
        found[(p.subject_class, p.object_class)].add(p.property_uri)
    for c in schema.collections or []:
        for member in c.member_types:
            found[(c.subject_class, member)].add(c.property_uri)
    return {pair: props for pair, props in found.items() if all(c.startswith(V) for c in pair)}


def rings(edges: dict[tuple[str, str], set[str]]) -> tuple[str, list[str], list[str]]:
    near = defaultdict(set)
    for a, b in edges:
        if a != b:
            near[a].add(b)
            near[b].add(a)
    hub = max(near, key=lambda c: (len(near[c]), c))
    (a, b), (c, d) = CONFIG["graph"]["inner"], CONFIG["graph"]["outer"]
    room = round((len(near) - 1) * (a + b) / (a + b + c + d))
    inner = sorted(sorted(near[hub], key=lambda n: (-len(near[n]), words(n)))[:room], key=words)
    outer = sorted(set(near) - {hub, *inner}, key=words)

    def spread(ring: list[str], around: list[str]) -> list[str]:
        at = {c: i / len(around) for i, c in enumerate(around)}
        return sorted(ring, key=lambda c: (sum(at[n] for n in near[c] if n in at) / max(1, len(near[c] & set(at))), words(c)))

    outer = spread(outer, inner)
    inner = spread(inner, outer) if outer else inner
    return hub, inner, spread(outer, inner)


def svg(edges: dict[tuple[str, str], set[str]]) -> tuple[str, str]:
    config = CONFIG["graph"]
    width, height = config["width"], config["height"]
    hub, inner, outer = rings(edges)
    folders = {t: c["folder"] for c in CONCEPTS["categories"] for t in c.get("types", [])}
    place = {hub: (width / 2, height / 2)}
    for ring, (rx, ry), turn in ((inner, config["inner"], 0.5), (outer, config["outer"], 0)):
        for i, c in enumerate(ring):
            angle = 2 * math.pi * (i + turn) / len(ring) - math.pi / 2
            place[c] = (width / 2 + rx * math.cos(angle), height / 2 + ry * math.sin(angle))
    ids = {c: f"n{i}" for i, c in enumerate([hub, *inner, *outer])}
    paths, rules = [], []
    for (a, b), props in sorted(edges.items()):
        if a == b:
            continue
        (x1, y1), (x2, y2) = place[a], place[b]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        pull = 0 if hub in (a, b) else config["pull"]
        cx, cy = mx + (width / 2 - mx) * pull, my + (height / 2 - my) * pull
        label = html.escape(f"{words(a)} → {', '.join(sorted(re.split(r'[/#]', p)[-1] for p in props))} → {words(b)}")
        paths.append(f'<path class="edge {ids[a]} {ids[b]}" d="M{x1:.0f},{y1:.0f} Q{cx:.0f},{cy:.0f} {x2:.0f},{y2:.0f}"><title>{label}</title></path>')
    nodes = []
    for c, (x, y) in place.items():
        lines = [html.escape(t) for t in re.findall(r".{1,%d}(?:\s|$)" % config["wrap"], words(c) + " ")]
        w = max(len(t) for t in lines) * config["char"] + 2 * config["pad"]
        h = len(lines) * config["line"] + config["pad"]
        text = "".join(f'<tspan x="{x:.0f}" dy="{config["line"] if i else config["line"] * (1 - len(lines)) / 2 + config["line"] / 3:.1f}">{t.strip()}</tspan>' for i, t in enumerate(lines))
        box = f'<rect x="{x - w / 2:.0f}" y="{y - h / 2:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{h / 2:.0f}"/><text x="{x:.0f}" y="{y:.0f}">{text}</text>'
        name = re.split(r"[/#]", c)[-1]
        near = sorted({ids[o] for pair in edges for o in pair if c in pair and o != c})
        css = " ".join(["node", ids[c], *(f"near-{n}" for n in near), *(["hub"] if c == hub else [])])
        target = f'/{folders[name]}/' if name in folders else c
        nodes.append(f'<a class="{css}" href="{html.escape(target)}"><title>{html.escape(words(c))}</title>{box}</a>')
        rules.append(
            f".schema-graph:has(.{ids[c]}.node:is(:hover, :focus-visible)) :is(.edge:not(.{ids[c]}), .node:not(.{ids[c]}, .near-{ids[c]})) {{ opacity: 0.12; }}"
        )
    drawing = (
        f'<svg class="schema-graph" viewBox="0 0 {width} {height}" role="img" aria-labelledby="diagram">'
        f'<g class="edges">{"".join(paths)}</g><g class="nodes">{"".join(nodes)}</g></svg>'
    )
    return drawing, "<style>" + "\n".join(rules) + "</style>"


def listing(edges: dict[tuple[str, str], set[str]]) -> str:
    rows = defaultdict(list)
    for (a, b), props in sorted(edges.items(), key=lambda x: (words(x[0][0]), words(x[0][1]))):
        rows[a].append(f'{html.escape(", ".join(sorted(re.split(r"[/#]", p)[-1] for p in props)))} <span class="concept-meta">→</span> {html.escape(words(b))}')
    items = "".join(f"<dt>{html.escape(words(a))}</dt><dd><ul>{''.join(f'<li>{x}</li>' for x in found)}</ul></dd>" for a, found in rows.items())
    return f'<details class="schema-list"><summary>{html.escape(CONFIG["list_title"])}</summary><dl class="facts">{items}</dl></details>'


def page(shell: str, edges: dict[tuple[str, str], set[str]]) -> str:
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    classes = len({c for pair in edges for c in pair})
    lede = CONFIG["lede"].format(classes=classes, links=len(edges))
    drawing, style = svg(edges)
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
<figure class="schema-figure">{drawing}</figure>{listing(edges)}</section>
<section class="cv-section" aria-labelledby="downloads"><h2 id="downloads">{html.escape(CONFIG["downloads_title"])}</h2>
<ul class="schema-downloads">{downloads}</ul></section>
</div>"""
    head = "\n".join([
        f"<title>{html.escape(CONFIG['title'])} · {html.escape(site['name'])}</title>",
        f'<meta name="description" content="{html.escape(lede)}" />',
        f'<link rel="canonical" href="{html.escape(site["canonical"] + CONFIG["folder"])}/" />',
        f'<link rel="describedby" type="application/json" href="{CONFIG["schema_file"]}" title="rdfsolve schema" />',
        style,
    ])
    return shell.replace("<!--concept:head-->", head).replace("<!--concept:body-->", body).replace("<!--concept:footer-->", "")


def main() -> None:
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    os.environ["RDFSOLVE_BASE_URI"] = site["canonical"] + CONFIG["void"]["base"]
    parts = graphs()
    schema = mine(ox.Dataset([q for part in parts.values() for q in part]))
    edges = links(schema)
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
    (folder / "index.html").write_text(page(shell, edges), encoding="utf-8")
    print(f"Wrote the schema page: {len(edges)} links, to {folder}")
    description = void(parts, schema).serialize(format="turtle")
    for name in CONFIG["void"]["files"]:
        (SITE / name).parent.mkdir(parents=True, exist_ok=True)
        (SITE / name).write_text(description, encoding="utf-8")
    print(f"Wrote the VoID description: {', '.join(CONFIG['void']['files'])}")


if __name__ == "__main__":
    main()
