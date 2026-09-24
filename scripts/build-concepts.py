"""Write a page for every concept in the site graph."""

import html
import json
import re
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import yaml
from rdflib import RDF, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(sys.argv[1] if len(sys.argv) > 1 else "_site")
CONFIG: dict[str, Any] = yaml.safe_load((ROOT / "_data/concepts.yml").read_text(encoding="utf-8"))
BASE: str = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))["canonical"]
V = Namespace(CONFIG["vocabulary"])
TERMS = {key: V[value] for key, value in CONFIG["terms"].items()}
# Internal names for merged unnamed nodes and index keys; never published.
MERGED = "urn:x-concept:"


def local(term: object) -> str:
    return str(term).removeprefix(str(V))


def terms(names: list[str]) -> set[URIRef]:
    return {V[n] for n in names}


def fill(template: str, **values: object) -> str:
    return template.format(**values)


def text(key: str, **values: object) -> str:
    return fill(CONFIG["text"][key], **values)


def slugify(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[: CONFIG["slug_length"]].strip("-") or "-"


def spell_out(name: str) -> str:
    """A term's local name as words: "courseCode" -> "Course code"."""
    return capital(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower())


def capital(value: str) -> str:
    return value[:1].upper() + value[1:]


def pretty_iri(iri: str) -> str:
    parsed = urlparse(iri)
    return (parsed.netloc + parsed.path).rstrip("/") or iri


def format_date(value: str) -> str:
    dates, parts = CONFIG["dates"], value.split("-")
    try:
        if len(parts) == 3:
            return fill(dates["day"], month=dates["months"][int(parts[1]) - 1], day=int(parts[2]), year=parts[0])
        if len(parts) == 2:
            return fill(dates["month"], month=dates["months"][int(parts[1]) - 1], year=parts[0])
    except (ValueError, IndexError):
        pass
    return html.escape(value)


def newest_first(value: str) -> tuple[int, str]:
    plain = re.sub("<[^>]+>", "", value)
    years = [int(y) for y in re.findall(r"\b\d{4}\b", plain)]
    return (-max(years, default=0), plain.casefold())


def definition_list(rows: dict[str, list[str]]) -> str:
    order = CONFIG["order"]
    items = []
    for label in sorted(rows, key=lambda x: (order.index(x) if x in order else len(order), x.casefold())):
        values = sorted(dict.fromkeys(rows[label]), key=newest_first)
        body = values[0] if len(values) == 1 else "<ul>" + "".join(f"<li>{v}</li>" for v in values) + "</ul>"
        items.append(f"<dt>{html.escape(label)}</dt><dd>{body}</dd>")
    return '<dl class="facts">' + "".join(items) + "</dl>"


class Concepts:
    def __init__(self, graph: Graph):
        profile = next(graph.subjects(RDF.type, V[CONFIG["profile"]["type"]]))
        self.me = graph.value(profile, V[CONFIG["profile"]["subject"]])
        self.aliases: dict[str, URIRef] = {}
        self.graph = self.merge(graph)
        g = self.graph
        self.owner = self.name(self.me)
        self.lists = set(g.subjects(RDF.first, None))
        excluded = terms(CONFIG["excluded_types"])
        self.nodes = sorted(
            {
                s for s in g.subjects(TERMS["name"], None)
                if isinstance(s, URIRef) and s != self.me and not str(s).startswith(BASE)
                and not excluded & set(g.objects(s, RDF.type))
            },
            key=lambda n: (self.name(n).casefold(), str(n)),
        )
        self.kind_predicates = [V[p] for p in CONFIG["kinds"]["predicates"]]
        self.paths: dict[URIRef, str] = {}
        self.kind_paths: dict[tuple[URIRef, str], str] = {}
        self.kind_homes: dict[tuple[URIRef, str], str] = {}  # the category folder each kind sorts
        taken: set[str] = set()

        def claim(folder: str, name: str) -> str:
            """A path unique within its folder: the name's slug, numbered if already taken."""
            slug = slugify(name)
            candidate, number = f"{folder}/{slug}/", 2
            while candidate in taken:
                candidate, number = f"{folder}/{slug}-{number}/", number + 1
            taken.add(candidate)
            return candidate

        for node in self.nodes:
            self.paths[node] = claim(self.category(node)["folder"], self.name(node))
        for predicate in self.kind_predicates:
            for value in sorted({str(o) for o in g.objects(None, predicate)}):
                home = self.category(self.members(predicate, value)[0])["folder"]
                self.kind_homes[(predicate, value)] = home
                self.kind_paths[(predicate, value)] = claim(f"{home}/{CONFIG['kinds']['folder']}", value)

    def kind_index(self, folder: str) -> str:
        """The page listing a category's kinds (/event/kind/)."""
        return f"{folder}/{CONFIG['kinds']['folder']}/"

    def kinds_in(self, folder: str) -> list[tuple[URIRef, str]]:
        return [key for key, home in self.kind_homes.items() if home == folder]

    def merge(self, graph: Graph) -> Graph:
        """Merge unnamed concept nodes that the pages describe separately, and unnamed nodes that
        share the name of an identified node of a `merge_by_name` type."""
        name, concept_types = TERMS["name"], set(CONFIG["unnamed_concept_types"])
        identified: dict[tuple[str, str], URIRef] = {}
        for kind in CONFIG["merge_by_name"]:
            for node in graph.subjects(RDF.type, V[kind]):
                if isinstance(node, URIRef) and graph.value(node, name) is not None:
                    identified[(kind, str(graph.value(node, name)))] = node
        keys: dict[BNode, URIRef] = {}
        for node in set(graph.subjects(name, None)):
            if not isinstance(node, BNode):
                continue
            kind = next((t for t in sorted(local(t) for t in graph.objects(node, RDF.type)) if t in concept_types), None)
            if kind is None:
                continue
            key = f"b:{kind}|{graph.value(node, name)}"
            same = identified.get((kind, str(graph.value(node, name))))
            if same is not None:
                keys[node] = same
                self.aliases[key] = same
            else:
                keys[node] = URIRef(MERGED + quote(key, safe=""))
        merged = Graph()
        for s, p, o in graph:
            merged.add((keys.get(s, s), p, keys.get(o, o)))  # type: ignore[arg-type]
        return merged

    # Naming, typing, and linking.
    def types(self, node: object) -> list[str]:
        return sorted(local(t) for t in self.graph.objects(node, RDF.type) if str(t).startswith(str(V)))  # type: ignore[arg-type]

    def names(self, node: object) -> list[str]:
        """Every name the sources give, the fullest first (most words, then longest)."""
        found = {str(n) for n in self.graph.objects(node, TERMS["name"])}  # type: ignore[arg-type]
        return sorted(found, key=lambda n: (-len(n.split()), -len(n), n))

    def name(self, node: object) -> str:
        names = self.names(node)
        return names[0] if names else pretty_iri(str(node))

    def labels(self, node: object) -> list[str]:
        labels = [CONFIG["type_labels"].get(t, spell_out(t)) for t in self.types(node)]
        if not labels:
            for predicate, label in CONFIG["untyped_labels"].items():
                if any(True for _ in self.graph.subjects(V[predicate], node)):  # type: ignore[arg-type]
                    labels.append(label)
        return labels or [CONFIG["untyped_default"]]

    def category(self, node: object) -> dict[str, Any]:
        types, labels = set(self.types(node)), set(self.labels(node))
        for category in CONFIG["categories"]:
            if types & set(category.get("types", [])) or labels & set(category.get("labels", [])):
                return category
        return CONFIG["other_category"]

    def href(self, node: object) -> str | None:
        if node == self.me:
            return CONFIG["home"]
        if node in self.paths:
            return "/" + self.paths[node]  # type: ignore[index]
        if isinstance(node, URIRef) and not str(node).startswith(MERGED):
            return str(node)
        return None

    def link(self, node: object, label: str | None = None) -> str:
        shown = html.escape(label or self.name(node))
        target = self.href(node)
        if not target:
            return shown
        css = "" if target.startswith("/") else ' class="external"'
        return f'<a href="{html.escape(target)}"{css}>{shown}</a>'

    def kind_link(self, predicate: URIRef, value: str) -> str:
        return f'<a href="/{self.kind_paths[(predicate, value)]}">{html.escape(capital(value))}</a>'

    # Values.
    def literal(self, value: Literal) -> str:
        if value.datatype in (XSD.date, XSD.gYearMonth, XSD.gYear):
            return format_date(str(value))
        if value.datatype == XSD.integer:
            return f"{int(str(value)):,}"
        return html.escape(str(value))

    def is_paper(self, node: object) -> bool:
        return bool(set(self.types(node)) & set(CONFIG["citation"]["types"]))

    def citation(self, node: object, keep: object = None) -> str:
        """A paper as in the CV's list: title; authors; venue · date · kind · identifier. A
        shortened author list always keeps `keep` (the person whose page this is)."""
        g, config = self.graph, CONFIG["citation"]
        number = g.value(node, V[config["number"]])  # type: ignore[arg-type]
        title = f"{number} {self.name(node)}" if number is not None else self.name(node)
        parts = [f'<span class="publication-title">{self.link(node, title)}</span>']
        head = g.value(node, V[config["authors"]])  # type: ignore[arg-type]
        if head is not None and head in self.lists:
            people = list(Collection(g, head))  # type: ignore[arg-type]
            over, first = config["shorten"]["over"], config["shorten"]["first"]
            shown, skipped = [], False
            for i, person in enumerate(people):
                if len(people) > over and not (i < first or i == len(people) - 1 or person in (self.me, keep)):
                    skipped = True
                    continue
                name = f"<strong>{self.link(person)}</strong>" if person == self.me else self.link(person)
                shown.append((config["gap"] if skipped else ", ") + name if shown else name)
                skipped = False
            parts.append(f'<span class="publication-authors">{"".join(shown)}</span>')
        details = []
        venue = g.value(node, V[config["venue"]])  # type: ignore[arg-type]
        if venue is not None:
            details.append(f"<em>{self.link(venue)}</em>")
        for predicate in config["dates"]:
            when = g.value(node, V[predicate])  # type: ignore[arg-type]
            if when is not None:
                details.append(format_date(str(when)))
                break
        kind = g.value(node, V[config["kind"]])  # type: ignore[arg-type]
        if kind is not None and (V[config["kind"]], str(kind)) in self.kind_paths:
            details.append(self.kind_link(V[config["kind"]], str(kind)))
        identifier = g.value(node, V[config["identifier"]])  # type: ignore[arg-type]
        if identifier is not None and isinstance(node, URIRef) and not str(node).startswith(MERGED):
            details.append(f'<a class="external" href="{html.escape(str(node))}">{html.escape(str(identifier))}</a>')
        if details:
            parts.append(f'<span class="publication-details">{" · ".join(details)}</span>')
        return "".join(parts)

    def value(self, node: object, around: object = None) -> str:
        if isinstance(node, Literal):
            return self.literal(node)
        if node in self.lists:
            return ", ".join(self.value(item) for item in Collection(self.graph, node))  # type: ignore[arg-type]
        if isinstance(node, BNode):
            return self.summary(node, around)
        if node == around:  # e.g. a repository that is also the software's identifier
            return f'<a class="external" href="{html.escape(str(node))}">{html.escape(pretty_iri(str(node)))}</a>'
        if self.is_paper(node):
            return self.citation(node)
        return self.link(node)

    def dates(self, node: object) -> str:
        g, config = self.graph, CONFIG["dates"]
        start, end = g.value(node, V[config["start"]]), g.value(node, V[config["end"]])  # type: ignore[arg-type]
        if start is not None:
            a = str(start).split("-")
            if end is not None and str(end) != str(start):
                b = str(end).split("-")
                if len(a) == len(b) == 3 and a[:2] == b[:2]:
                    return fill(config["range_in_month"], month=config["months"][int(a[1]) - 1], start=int(a[2]), end=int(b[2]), year=a[0])
                return fill(config["range"], start=format_date(str(start)), end=format_date(str(end)))
            if set(self.types(node)) & set(config["open_ended_types"]):
                return fill(config["range"], start=format_date(str(start)), end=config["open_ended"])
            return format_date(str(start))
        for predicate in config["single"]:
            when = g.value(node, V[predicate])  # type: ignore[arg-type]
            if when is not None:
                return format_date(str(when))
        return ""

    def summary(self, node: BNode, around: object = None) -> str:
        """An unnamed node (a role, grant, counter, identifier) in one line."""
        g, config = self.graph, CONFIG["summary"]
        types = set(self.types(node))
        counter, identifier = config["counter"], config["identifier"]
        if counter["type"] in types:
            count = g.value(node, V[counter["count"]])
            shown = f"{int(str(count)):,} {g.value(node, TERMS['name']) or ''}".strip()
            source = g.value(node, V[counter["source"]])
            return self.link(source, shown) if source else html.escape(shown)
        if identifier["type"] in types:
            return html.escape(f"{g.value(node, V[identifier['scheme']])}: {g.value(node, V[identifier['value']])}")
        parts = []
        for predicate in config["headline"]:
            headline = sorted(str(v) for v in g.objects(node, V[predicate]) if isinstance(v, Literal))
            if headline:
                parts.append(html.escape(", ".join(headline)))
                break
        skip = terms(config["skip"]) | {RDF.type}
        papers = []
        for p, o in sorted(g.predicate_objects(node), key=lambda po: str(po[0])):
            if o != around and p not in skip and isinstance(o, URIRef) and (o in self.paths or o == self.me):
                if self.is_paper(o):
                    papers.append(f'<span class="concept-citation">{self.citation(o)}</span>')
                else:
                    parts.append(self.link(o))
        when = self.dates(node)
        if when:
            parts.append(when)
        line = " · ".join(parts)
        # A node may carry several notes (a role's status and paragraphs), unordered in the
        # graph; the shortest is the one that reads as a one-line note.
        notes = sorted((str(n).strip() for n in g.objects(node, V[config["note"]])), key=len)
        if notes:
            line += f'<span class="concept-note">{html.escape(capital(notes[0]))}</span>'
        return line + "".join(papers)

    @staticmethod
    def join(lead: str, detail: str) -> str:
        """A lead and its detail, the dot separator only before inline text (not before a note)."""
        if not detail:
            return lead
        return lead + ("" if detail.startswith('<span class="concept-note">') else " · ") + detail

    # Statements about a concept.
    def outgoing(self, node: URIRef) -> dict[str, list[str]]:
        g, rows = self.graph, defaultdict(list)
        dates = CONFIG["dates"]
        hidden = terms(CONFIG["header"]) | {V[dates["start"]], V[dates["end"]]}
        for p, o in g.predicate_objects(node):
            if not str(p).startswith(str(V)) or p in hidden:
                continue
            if p in self.kind_predicates:
                rows[CONFIG["kinds"]["label"]].append(self.kind_link(p, str(o)))  # type: ignore[arg-type]
                continue
            name = local(p)
            label = CONFIG["forward"].get(name, spell_out(name))
            for kind, special in CONFIG["forward_by_object_type"].get(name, {}).items():
                if kind in self.types(o):
                    label = special
            rows[label].append(self.value(o, around=node))
        if g.value(node, V[dates["start"]]) is not None:
            rows[dates["label"]].append(self.dates(node))
        return self.drop_plain(rows)

    @staticmethod
    def drop_plain(rows: dict[str, list[str]]) -> dict[str, list[str]]:
        """A bare link is dropped where the same row also has it with more said (a role)."""
        for values in rows.values():
            for plain in [v for v in values if any(o != v and o.startswith(v) for o in values)]:
                values.remove(plain)
        return rows

    def reverse_label(self, predicate: object, subject: object) -> str:
        name = local(predicate)
        for kind, special in CONFIG["reverse_by_subject_type"].get(name, {}).items():
            if kind in self.types(subject):
                return special
        return CONFIG["reverse"].get(name) or fill(CONFIG["reverse_default"], label=spell_out(name))

    def incoming(self, node: URIRef) -> dict[str, list[str]]:
        g, rows = self.graph, defaultdict(list)
        skip = terms(CONFIG["reverse_skip"])
        for s, p in g.subject_predicates(node):
            in_list = p == RDF.first and s in self.lists
            if not in_list and (not str(p).startswith(str(V)) or p in skip):
                continue
            if in_list:  # a member of an ordered list (e.g. authors): the thing the list belongs to
                head = s
                while (found := g.value(None, RDF.rest, head)) is not None:
                    head = found
                for owner, q in g.subject_predicates(head):
                    rows[self.reverse_label(q, owner)].append(self.entry(owner, keep=node))
            elif isinstance(s, BNode):  # a role, grant, or instance: read through to what it belongs to
                anchor = self.anchor(s, node)
                detail = self.summary(s, around=node)
                rows[self.reverse_label(p, s)].append(detail if anchor is None else self.join(self.link(anchor), detail))
            elif s != node and not str(s).startswith(BASE):
                rows[self.reverse_label(p, s)].append(self.entry(s))
        # Roles held at what points here (e.g. studying at a university based in this place).
        activity = self.category(node).get("activity")
        if activity:
            for place_holder in g.subjects(V[activity["through"]], node):
                for predicate in activity["predicates"]:
                    for role in g.subjects(V[predicate], place_holder):
                        if isinstance(role, BNode) and role not in self.lists:
                            rows[activity["label"]].append(self.join(self.link(place_holder), self.summary(role, around=place_holder)))
        # The graph's subject stated directly (e.g. works for) is dropped where a role says more.
        plain = self.entry(self.me)
        for values in rows.values():
            if plain in values and any(v != plain and v.startswith(self.link(self.me)) for v in values):
                values.remove(plain)
        return rows

    def anchor(self, node: BNode, around: object, seen: frozenset = frozenset()) -> object:
        """The nearest named thing an unnamed node belongs to: its owner or, through a role, what
        the role was in. The graph's subject is the fallback, since everything is about them."""
        g = self.graph
        owners = [o for o in g.subjects(None, node) if o not in self.lists]
        for owner in owners:
            if not isinstance(owner, BNode) and owner != self.me:
                return owner
        for owner in owners:
            if isinstance(owner, BNode) and owner not in seen:
                for p, target in g.predicate_objects(owner):
                    if p != RDF.type and target in self.paths and target != around:
                        return target
                found = self.anchor(owner, around, seen | {node})
                if found is not None:
                    return found
        return self.me if self.me in owners or not owners else None

    def entry(self, node: object, keep: object = None) -> str:
        if node == self.me:
            return self.link(node)
        if self.is_paper(node):
            return self.citation(node, keep)
        labels = self.labels(node)
        meta = " · ".join(x for x in (labels[0] if labels != [CONFIG["untyped_default"]] else "", self.dates(node)) if x)
        return self.link(node) + (f' <span class="concept-meta">{meta}</span>' if meta else "")

    def neighbors(self, node: object) -> set[object]:
        """The concepts a node is connected to, directly or through unnamed nodes (roles, lists)."""
        g, found, seen = self.graph, set(), {node}
        frontier = [node]
        while frontier:
            current = frontier.pop()
            adjacent = [*g.objects(current, None), *g.subjects(None, current)]  # type: ignore[arg-type]
            for other in adjacent:
                if other in seen:
                    continue
                seen.add(other)
                if other in self.paths:
                    found.add(other)
                elif isinstance(other, BNode):
                    frontier.append(other)
        return found

    def year(self, node: object) -> str:
        years = re.findall(r"\b\d{4}\b", re.sub("<[^>]+>", "", self.dates(node)))
        return years[0] if years else ""

    def members(self, predicate: URIRef, value: str) -> list[object]:
        return [s for s, o in self.graph.subject_objects(predicate) if str(o) == value]

    def identity(self, node: URIRef) -> list[str]:
        g, links = self.graph, []
        targets = [node, *sorted(g.objects(node, TERMS["same_as"]), key=str), *g.objects(node, TERMS["url"])]
        for target in targets:
            if not isinstance(target, URIRef) or str(target).startswith(MERGED):
                continue
            if g.value(target, URIRef(CONFIG["link_title"])) is not None:
                continue  # a named profile, listed under "elsewhere"
            host = urlparse(str(target)).netloc
            label = CONFIG["hosts"].get(host) or host.removeprefix("www.") or CONFIG["host_default"]
            source = g.value(target, URIRef(CONFIG["provenance"]))
            if source is not None:
                label = fill(CONFIG["text"]["from_source"], label=label, source=CONFIG["hosts"].get(urlparse(str(source)).netloc, pretty_iri(str(source))))
            item = f'<a class="external" href="{html.escape(str(target))}">{html.escape(label)}</a>'
            if item not in links:
                links.append(item)
        label = CONFIG["identifier_label"]
        for identifier in g.objects(node, TERMS["identifier"]):
            if isinstance(identifier, Literal) and not any(f">{label}<" in x for x in links):
                links.append(f"{label} {html.escape(str(identifier))}")
        return links

    def elsewhere(self, node: URIRef) -> dict[str, list[str]]:
        """Named profiles on other services, by the source that names them."""
        g, found = self.graph, defaultdict(list)
        for target in sorted(g.objects(node, TERMS["same_as"]), key=lambda t: str(g.value(t, URIRef(CONFIG["link_title"])) or "").casefold()):
            title = g.value(target, URIRef(CONFIG["link_title"]))
            if title is None:
                continue
            source = g.value(target, URIRef(CONFIG["provenance"]))
            where = CONFIG["hosts"].get(urlparse(str(source)).netloc, pretty_iri(str(source))) if source is not None else ""
            found[where].append(f'<a class="external" href="{html.escape(str(target))}">{html.escape(str(title))}</a>')
        return found

    # Turtle.
    def publish(self, statements: Graph) -> Graph:
        """Merged unnamed nodes go back to being blank nodes: nothing is minted."""
        published, blank = Graph(), {}
        published.bind(CONFIG["vocabulary_prefix"], V)
        for s, p, o in statements:
            s2 = blank.setdefault(s, BNode()) if str(s).startswith(MERGED) else s
            o2 = blank.setdefault(o, BNode()) if str(o).startswith(MERGED) else o
            published.add((s2, p, o2))  # type: ignore[arg-type]
        return published

    def statements(self, node: URIRef) -> Graph:
        """The concept's own statements and those pointing to it, with the unnamed nodes between."""
        g, out, seen = self.graph, Graph(), set()

        def closure(n: object) -> None:
            if n in seen:
                return
            seen.add(n)
            for p, o in g.predicate_objects(n):  # type: ignore[arg-type]
                out.add((n, p, o))  # type: ignore[arg-type]
                if isinstance(o, BNode):
                    closure(o)

        closure(node)
        for s, p in g.subject_predicates(node):
            out.add((s, p, node))
            if isinstance(s, BNode):
                closure(s)
                for owner, q in g.subject_predicates(s):
                    out.add((owner, q, s))
        return self.publish(out)

    def listing(self, members: Sequence[object], predicate: URIRef | None = None) -> Graph:
        """Members' types and names (and kind), for a category's or kind's Turtle."""
        out = Graph()
        for member in members:
            for p in [RDF.type, TERMS["name"], *([predicate] if predicate else [])]:
                for o in self.graph.objects(member, p):  # type: ignore[arg-type]
                    out.add((member, p, o))  # type: ignore[arg-type]
        return self.publish(out)


def document(concepts: Concepts, statements: Graph, page: str, name: str, about: object) -> Graph:
    """What a page offers machines: the statements it shows, plus the page itself (what it is,
    what it is about, who it is by)."""
    config, doc = CONFIG["signposting"], Graph()
    doc += statements
    doc.bind(CONFIG["vocabulary_prefix"], V)
    node = URIRef(page)
    for kind in config["page_types"]:
        doc.add((node, RDF.type, V[kind]))
    doc.add((node, TERMS["name"], Literal(name, lang="en")))
    if isinstance(about, URIRef) and not str(about).startswith(MERGED):
        doc.add((node, V[config["about"]], about))
    if isinstance(concepts.me, URIRef):
        doc.add((node, V[config["author"]], concepts.me))
    return doc


def signposts(concepts: Concepts, about: object) -> list[str]:
    """FAIR Signposting in the head: each format, the page's and its subject's types, the author,
    and a persistent identifier to cite, when the subject has one."""
    config = CONFIG["signposting"]
    links = [
        f'<link rel="describedby alternate" type="{f["type"]}" href="{f["file"]}" title="{html.escape(f["name"])}" />'
        for f in CONFIG["formats"]
    ]
    types = [V[t] for t in config["page_types"]]
    if about is not None:
        types += [V[t] for t in concepts.types(about)]
    links += [f'<link rel="type" href="{html.escape(str(t))}" />' for t in dict.fromkeys(types)]
    if isinstance(concepts.me, URIRef):
        links.append(f'<link rel="author" href="{html.escape(str(concepts.me))}" />')
    if isinstance(about, URIRef) and urlparse(str(about)).netloc in config["persistent_hosts"]:
        links.append(f'<link rel="cite-as" href="{html.escape(str(about))}" />')
    return links


def frame(
    shell: str, concepts: Concepts, name: str, summary: str, page: str, body: list[str],
    statements: Graph, about: object = None, current: str | None = None, scripts: Sequence[str] = (),
) -> tuple[str, Graph]:
    """A page in the site's frame, with its graph. `current` marks that link in the top bar."""
    doc = document(concepts, statements, page, name, about)
    embedded = doc.serialize(format="json-ld", context={"@vocab": str(V), CONFIG["vocabulary_prefix"]: str(V)})
    safe = embedded.replace("</", "<\\/")  # a script element must not contain "</"
    head = "\n".join([
        f"<title>{html.escape(text('title', name=name, owner=concepts.owner))}</title>",
        f'<meta name="description" content="{html.escape(summary[:300])}" />',
        f'<link rel="canonical" href="{html.escape(page)}" />',
        *signposts(concepts, about),
        f'<script type="application/ld+json">{safe}</script>',
        *(f'<script src="{src}" defer></script>' for src in scripts),
    ])
    if current:
        shell = shell.replace(f'href="{current}"', f'href="{current}" aria-current="page"', 1)
    formats = ", ".join(f'<a href="{f["file"]}" type="{f["type"]}">{html.escape(f["name"])}</a>' for f in CONFIG["formats"])
    page_html = (
        shell.replace("<!--concept:head-->", head)
        .replace("<!--concept:body-->", "\n".join(body))
        .replace("<!--concept:footer-->", text("footer", formats=formats))
    )
    return page_html, doc


def header(kicker: str, title: str, *extra: str) -> list[str]:
    return ['<div class="cv concept">', '<header class="cv-header concept-header">',
            f'<p class="concept-kind">{kicker}</p>', f"<h1>{html.escape(title)}</h1>", *extra, "</header>"]


def section(heading: str | None, rows: dict[str, list[str]]) -> str:
    if not rows:
        return ""
    if heading is None:
        return f'<section class="cv-section">{definition_list(rows)}</section>'
    anchor = slugify(heading)
    return f'<section class="cv-section" aria-labelledby="{anchor}"><h2 id="{anchor}">{html.escape(heading)}</h2>{definition_list(rows)}</section>'


def render(concepts: Concepts, node: URIRef, shell: str) -> tuple[str, Graph]:
    g = concepts.graph
    name, labels = concepts.name(node), concepts.labels(node)
    description = g.value(node, TERMS["description"])
    alternate = sorted(str(a) for a in g.objects(node, TERMS["alternate_name"]))
    identity = concepts.identity(node)
    extra = []
    if alternate:
        extra.append(f'<p class="concept-alternate">{html.escape(text("also_known_as", names=", ".join(alternate)))}</p>')
    listed = concepts.names(node)[1:]
    if listed:
        extra.append(f'<p class="concept-alternate">{html.escape(text("also_listed_as", names=", ".join(listed)))}</p>')
    if description is not None:
        extra.append(f'<p class="concept-description">{html.escape(str(description))}</p>')
    if identity:
        extra.append('<p class="concept-identity">' + " · ".join(identity) + "</p>")
    for source, links in concepts.elsewhere(node).items():
        extra.append(f'<p class="concept-identity concept-elsewhere">{text("elsewhere", source=html.escape(source), links=" · ".join(links))}</p>')
    kicker = f'<a href="/{concepts.category(node)["folder"]}/">{html.escape(" · ".join(labels))}</a>'
    body = [
        *header(kicker, name, *extra),
        section(text("details"), concepts.outgoing(node)),
        section(text("connections"), concepts.incoming(node)),
        "</div>",
    ]
    summary = str(description) if description is not None else text("summary", kind=labels[0], owner=concepts.owner)
    return frame(shell, concepts, name, summary, BASE + concepts.paths[node], body, concepts.statements(node), node)


def render_kind(concepts: Concepts, predicate: URIRef, value: str, shell: str) -> tuple[str, Graph]:
    """A kind (Hackathon, Journal article): everything of that kind, and the other kinds."""
    name, members = capital(value), concepts.members(predicate, value)
    rows: dict[str, list[str]] = defaultdict(list)
    for member in members:
        rows[concepts.labels(member)[0]].append(concepts.entry(member))
    of = sorted({concepts.labels(m)[0].lower() for m in members})
    others = [concepts.kind_link(p, v) for p, v in concepts.kind_paths if p == predicate and v != value]
    if others:
        rows[text("other_kinds")].append(" · ".join(others))
    home = concepts.kind_homes[(predicate, value)]
    kicker = f'<a href="/{concepts.kind_index(home)}">{html.escape(text("kind_of", of=" / ".join(of)))}</a>'
    body = [*header(kicker, name), section(text("connections"), rows), "</div>"]
    summary = text("kind_summary", name=name, count=len(members), owner=concepts.owner)
    page = BASE + concepts.kind_paths[(predicate, value)]
    return frame(shell, concepts, name, summary, page, body, concepts.listing(members, predicate))


def render_category(concepts: Concepts, category: dict[str, Any], members: list[URIRef], shell: str) -> tuple[str, Graph]:
    """A category: its members grouped by kind or label; with `lists`, each member with what
    points to it through that predicate (a place, and what is based or held there)."""
    g, title = concepts.graph, category["title"]
    if category.get("lists"):
        items = []
        for member in members:
            here = sorted({concepts.entry(s) for s in g.subjects(V[category["lists"]], member) if s in concepts.paths}, key=newest_first)
            listed = "<ul>" + "".join(f"<li>{h}</li>" for h in here) + "</ul>" if here else ""
            items.append(f"<dt>{concepts.link(member)}</dt><dd>{listed}</dd>")
        content = '<section class="cv-section"><dl class="facts">' + "".join(items) + "</dl></section>"
    else:
        rows: dict[str, list[str]] = defaultdict(list)
        for member in members:
            kind = next((str(o) for p in concepts.kind_predicates for o in g.objects(member, p)), None)
            rows[capital(kind) if kind else concepts.labels(member)[0]].append(concepts.entry(member))
        content = section(None, rows)
    kinds = concepts.kinds_in(category["folder"])
    by_kind = []
    if kinds:
        links = " · ".join(concepts.kind_link(p, v) for p, v in kinds)
        by_kind.append(f'<p class="concept-identity">{text("by_kind", links=links)}</p>')
    body = [*header(html.escape(text("category")), title, *by_kind), content, "</div>"]
    summary = text("category_summary", title=title, owner=concepts.owner)
    return frame(shell, concepts, title, summary, BASE + category["folder"] + "/", body, concepts.listing(members))


def render_kinds(concepts: Concepts, category: dict[str, Any], shell: str) -> tuple[str, Graph]:
    """A category's kinds (/event/kind/), each with how many things it sorts."""
    title = text("kinds_of", of=category["title"].lower())
    rows: dict[str, list[str]] = defaultdict(list)
    for predicate, value in concepts.kinds_in(category["folder"]):
        count = len(concepts.members(predicate, value))
        rows[CONFIG["kinds"]["label"]].append(f'{concepts.kind_link(predicate, value)} <span class="concept-meta">{count}</span>')
    kicker = f'<a href="/{category["folder"]}/">{html.escape(category["title"])}</a>'
    body = [*header(kicker, title), section(None, rows), "</div>"]
    summary = text("category_summary", title=title, owner=concepts.owner)
    members = [m for p, v in concepts.kinds_in(category["folder"]) for m in concepts.members(p, v)]
    return frame(shell, concepts, title, summary, BASE + concepts.kind_index(category["folder"]), body, concepts.listing(members))


def render_content(concepts: Concepts, grouped: dict[str, list[URIRef]], categories: dict[str, dict[str, Any]], shell: str) -> tuple[str, Graph]:
    """Everything as one table: the site's pages, then each category and its members, with each
    member's kind, year, and how many concepts it is connected to. A filter narrows it (content.js)."""
    config, g = CONFIG["content"], concepts.graph
    columns = config["columns"]
    head = (
        f'<thead><tr><th scope="col">{columns["name"]}</th><th scope="col">{columns["kind"]}</th>'
        f'<th scope="col" class="num">{columns["year"]}</th>'
        f'<th scope="col" class="num" title="{html.escape(config["links_hint"])}">{columns["links"]}</th></tr></thead>'
    )

    def group(title: str, href: str | None, rows: list[str], css: str = "content-group") -> str:
        label = f'<a href="{href}">{html.escape(title)}</a>' if href else html.escape(title)
        return (
            f'<tbody class="{css}"><tr class="content-group-head"><th colspan="4" scope="colgroup">{label}'
            f' <span class="content-group-count">{len(rows)}</span></th></tr>{"".join(rows)}</tbody>'
        )

    def row(link: str, kind: str, year: str, links: str) -> str:
        return f'<tr><td>{link}</td><td class="content-kind">{kind}</td><td class="num">{year}</td><td class="num">{links}</td></tr>'

    pages = [row(f'<a href="{p["path"]}">{html.escape(p["name"])}</a>', "", "", "") for p in config["pages"]]
    groups = [group(config["pages_title"], None, pages, "content-group content-pages")]
    order = [c["folder"] for c in [*CONFIG["categories"], CONFIG["other_category"]]]
    total = 0
    for folder in sorted(grouped, key=order.index):
        rows = []
        for member in grouped[folder]:
            kind = next(
                (concepts.kind_link(p, str(o)) for p in concepts.kind_predicates for o in g.objects(member, p) if (p, str(o)) in concepts.kind_paths),
                html.escape(concepts.labels(member)[0]),
            )
            rows.append(row(concepts.link(member), kind, concepts.year(member), str(len(concepts.neighbors(member)))))
        total += len(rows)
        groups.append(group(categories[folder]["title"], f"/{folder}/", rows))
    lede = html.escape(fill(config["lede"], count=total, categories=len(grouped)))
    toolbar = (
        f'<div class="content-toolbar" hidden><input class="content-filter" type="search" placeholder="{html.escape(config["filter"])}"'
        f' aria-label="{html.escape(config["filter"])}" /><span class="content-count" aria-live="polite"'
        f' data-template="{html.escape(config["shown"])}"></span></div>'
    )
    body = [
        *header(html.escape(config["kicker"]), config["title"], f'<p class="concept-description">{lede}</p>'),
        f'<section class="cv-section content-section">{toolbar}<table class="content-table">{head}{"".join(groups)}</table></section>',
        "</div>",
    ]
    summary = fill(config["lede"], count=total, categories=len(grouped))
    return frame(shell, concepts, config["title"], summary, BASE + config["folder"] + "/", body,
                 concepts.listing(concepts.nodes), current=config["nav"], scripts=["/assets/js/content.js"])


def merge_sitemap(urls: list[str], stale: list[str], lastmod: str) -> None:
    """Add pages to the site's sitemap, replacing any from an earlier run."""
    sitemap = SITE / CONFIG["files"]["sitemap"]
    existing = sitemap.read_text(encoding="utf-8")
    drop = set(urls) | set(stale)
    kept = [u for u in re.findall(r"<url>.*?</url>", existing, re.DOTALL) if re.search(r"<loc>(.*?)</loc>", u).group(1) not in drop]  # type: ignore[union-attr]
    added = [f"<url><loc>{u}</loc>{lastmod}</url>" for u in urls]
    head = existing.split("<url>")[0].rstrip() if "<url>" in existing else existing.split("</urlset>")[0].rstrip()
    sitemap.write_text(head + "\n  " + "\n  ".join(kept + added) + "\n</urlset>\n", encoding="utf-8")


def write(folder: str, rendered: tuple[str, Graph]) -> None:
    """A page and its graph in every configured format, side by side."""
    page, graph = rendered
    (SITE / folder).mkdir(parents=True, exist_ok=True)
    (SITE / folder / "index.html").write_text(page, encoding="utf-8")
    for f in CONFIG["formats"]:
        (SITE / folder / f["file"]).write_text(graph.serialize(format=f["rdflib"]), encoding="utf-8")


def clear_previous() -> list[str]:
    """Remove what the last run wrote, so renamed concepts leave no stale pages when serving.
    Returns the pages removed."""
    manifest = SITE / CONFIG["files"]["manifest"]
    if not manifest.exists():
        return []
    previous: list[str] = json.loads(manifest.read_text(encoding="utf-8"))
    # Deepest first, so a category's folder is empty by the time it is reached.
    for path in sorted(previous, key=lambda p: -p.count("/")):
        target = SITE / path
        for name in ("index.html", *(f["file"] for f in CONFIG["formats"])):
            (target / name).unlink(missing_ok=True)
        if target.is_dir() and not any(target.iterdir()):
            target.rmdir()
    return previous


def main() -> None:
    previous = clear_previous()
    graph = Graph()
    for source in CONFIG["sources"]:
        graph.parse(SITE / source, format="json-ld")
    concepts = Concepts(graph)
    files = CONFIG["files"]
    shell = (SITE / files["shell"]).read_text(encoding="utf-8")
    index: dict[str, dict[str, str]] = {}

    for node in concepts.nodes:
        write(concepts.paths[node], render(concepts, node, shell))
        key = unquote(str(node).removeprefix(MERGED)) if str(node).startswith(MERGED) else str(node)
        index[key] = {"path": "/" + concepts.paths[node], "name": concepts.name(node)}
    for key, node in concepts.aliases.items():
        if node in concepts.paths:
            index[key] = {"path": "/" + concepts.paths[node], "name": concepts.name(node)}
    for (predicate, value), path in concepts.kind_paths.items():
        write(path, render_kind(concepts, predicate, value, shell))
        index[f"kind:{local(predicate)}|{value}"] = {"path": "/" + path, "name": capital(value)}

    hubs = []
    grouped: dict[str, list[URIRef]] = defaultdict(list)
    categories = {c["folder"]: c for c in [*CONFIG["categories"], CONFIG["other_category"]]}
    for node in concepts.nodes:
        grouped[concepts.category(node)["folder"]].append(node)
    for folder, members in sorted(grouped.items()):
        if not categories[folder].get("existing"):
            write(f"{folder}/", render_category(concepts, categories[folder], members, shell))
            hubs.append(f"{folder}/")
    for folder in sorted(set(concepts.kind_homes.values())):
        write(concepts.kind_index(folder), render_kinds(concepts, categories[folder], shell))
        hubs.append(concepts.kind_index(folder))

    content = f"{CONFIG['content']['folder']}/"
    write(content, render_content(concepts, grouped, categories, shell))
    hubs.append(content)

    paths = [*hubs, *concepts.paths.values(), *concepts.kind_paths.values()]
    (SITE / files["index"]).write_text(json.dumps(index, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (SITE / files["manifest"]).write_text(json.dumps(paths, indent=0), encoding="utf-8")
    profile = next(graph.subjects(RDF.type, V[CONFIG["profile"]["type"]]))
    modified = graph.value(profile, V[CONFIG["profile"]["modified"]])
    lastmod = f"<lastmod>{modified}</lastmod>" if modified else ""
    merge_sitemap([BASE + path for path in paths], [BASE + path for path in previous], lastmod)
    print(f"Wrote {len(concepts.nodes)} concept, {len(concepts.kind_paths)} kind, and {len(hubs)} category pages to {SITE}")


if __name__ == "__main__":
    main()
