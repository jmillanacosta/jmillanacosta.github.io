"""Concept pages and RDF files are generated from the merged site graph."""

import html
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import pyoxigraph as ox
import yaml
from rdflib import Graph
from rdfsolve.schema_models import MinedSchema
from rdfsolve.schema_models.exporters.paths import path_to_sparql
from rdfsolve.schema_models.paths import PropertyPath

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(sys.argv[1] if len(sys.argv) > 1 else "_site").resolve()
CONFIG: dict[str, Any] = yaml.safe_load((ROOT / "_data/concepts.yml").read_text(encoding="utf-8"))
BASE: str = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))["canonical"]
V: str = CONFIG["vocabulary"]
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XSD = "http://www.w3.org/2001/XMLSchema#"
TYPE, FIRST, REST, NIL = (ox.NamedNode(RDF + name) for name in ("type", "first", "rest", "nil"))
DATES = {XSD + "date", XSD + "gYearMonth", XSD + "gYear"}
# Internal names for merged unnamed nodes and index keys. They are never published.
MERGED = "urn:x-concept:"

Node = ox.NamedNode | ox.BlankNode | ox.Literal
Leaf = tuple[str, Node | None]


def v(name: str) -> ox.NamedNode:
    """A term of the vocabulary of the site (schema.org)."""
    return ox.NamedNode(V + name)


TERMS = {key: v(value) for key, value in CONFIG["terms"].items()}


def local(term: ox.NamedNode) -> str:
    return term.value.removeprefix(V)


def fill(template: str, **values: object) -> str:
    return template.format(**values)


def text(key: str, **values: object) -> str:
    return fill(CONFIG["text"][key], **values)


def slugify(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[: CONFIG["slug_length"]].strip("-") or "-"


def spell_out(name: str) -> str:
    """The local name of a term as words: "courseCode" gives "Course code"."""
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


class Data:
    """The statements of the site graph, in an Oxigraph dataset."""

    def __init__(self, quads: Iterable[ox.Quad]):
        self.dataset = ox.Dataset(ox.Quad(q.subject, q.predicate, q.object) for q in quads)

    def out(self, subject: object) -> list[ox.Quad]:
        if not isinstance(subject, (ox.NamedNode, ox.BlankNode)):
            return []
        return list(self.dataset.quads_for_subject(subject))

    def into(self, value: object) -> list[ox.Quad]:
        if not isinstance(value, (ox.NamedNode, ox.BlankNode, ox.Literal)):
            return []
        return list(self.dataset.quads_for_object(value))

    def objects(self, subject: object, predicate: ox.NamedNode) -> list[Node]:
        return [q.object for q in self.out(subject) if q.predicate == predicate]

    def subjects(self, predicate: ox.NamedNode, value: object = None) -> list[Node]:
        quads = self.dataset.quads_for_predicate(predicate) if value is None else self.into(value)
        return [q.subject for q in quads if q.predicate == predicate]

    def value(self, subject: object, predicate: ox.NamedNode) -> Node | None:
        """One value: the first in the order of the text when there are several."""
        return min(self.objects(subject, predicate), key=str, default=None)

    def members(self, head: object) -> list[Node]:
        """The members of an RDF list, in order."""
        found = []
        while head is not None and head != NIL:
            found += self.objects(head, FIRST)[:1]
            head = self.value(head, REST)
        return found


class Concepts:
    def __init__(self, data: Data):
        profile = data.subjects(TYPE, v(CONFIG["profile"]["type"]))[0]
        self.me = data.value(profile, v(CONFIG["profile"]["subject"]))
        self.aliases: dict[str, ox.NamedNode] = {}
        self.data = self.merge(data)
        d = self.data
        self.owner = self.name(self.me)
        self.lists = set(d.subjects(FIRST))
        excluded = {v(t) for t in CONFIG["excluded_types"]}
        self.nodes = sorted(
            {
                s for s in d.subjects(TERMS["name"])
                if isinstance(s, ox.NamedNode) and s != self.me and not s.value.startswith(BASE)
                and not excluded & set(d.objects(s, TYPE))
            },
            key=lambda n: (self.name(n).casefold(), n.value),
        )
        self.kind_predicates = [v(p) for p in CONFIG["kinds"]["predicates"]]
        self.paths: dict[Node, str] = {}
        self.kind_paths: dict[tuple[ox.NamedNode, str], str] = {}
        self.kind_homes: dict[tuple[ox.NamedNode, str], str] = {}  # the category folder of each kind
        self._neighbors: dict[Node, set[Node]] = {}
        self._direct: dict[Node, set[Node]] = {}
        taken: set[str] = set()

        def claim(folder: str, name: str) -> str:
            """A path in a folder: the slug of the name, with a number if it is already used."""
            slug = slugify(name)
            candidate, number = f"{folder}/{slug}/", 2
            while candidate in taken:
                candidate, number = f"{folder}/{slug}-{number}/", number + 1
            taken.add(candidate)
            return candidate

        for node in self.nodes:
            self.paths[node] = claim(self.category(node)["folder"], self.name(node))
        for predicate in self.kind_predicates:
            for value in sorted({q.object.value for q in d.dataset.quads_for_predicate(predicate)}):
                home = self.category(self.members(predicate, value)[0])["folder"]
                self.kind_homes[(predicate, value)] = home
                self.kind_paths[(predicate, value)] = claim(f"{home}/{CONFIG['kinds']['folder']}", value)

    def kind_index(self, folder: str) -> str:
        """The page that lists the kinds of a category (/event/kind/)."""
        return f"{folder}/{CONFIG['kinds']['folder']}/"

    def kinds_in(self, folder: str) -> list[tuple[ox.NamedNode, str]]:
        return [key for key, home in self.kind_homes.items() if home == folder]

    def merge(self, data: Data) -> Data:
        """Unnamed concepts are merged by type and name."""
        name, concept_types = TERMS["name"], set(CONFIG["unnamed_concept_types"])
        identified: dict[tuple[str, str], ox.NamedNode] = {}
        for kind in CONFIG["merge_by_name"]:
            for node in data.subjects(TYPE, v(kind)):
                if isinstance(node, ox.NamedNode) and data.value(node, name) is not None:
                    identified[(kind, data.value(node, name).value)] = node
        keys: dict[Node, ox.NamedNode] = {}
        for node in set(data.subjects(name)):
            if not isinstance(node, ox.BlankNode):
                continue
            kind = next((t for t in sorted(local(t) for t in data.objects(node, TYPE)) if t in concept_types), None)
            if kind is None:
                continue
            node_name = data.value(node, name).value
            key = f"b:{kind}|{node_name}"
            same = identified.get((kind, node_name))
            if same is not None:
                keys[node] = same
                self.aliases[key] = same
            else:
                keys[node] = ox.NamedNode(MERGED + quote(key, safe=""))
        return Data(ox.Quad(keys.get(q.subject, q.subject), q.predicate, keys.get(q.object, q.object)) for q in data.dataset)

    # Names, types and links.
    def types(self, node: object) -> list[str]:
        return sorted(local(t) for t in self.data.objects(node, TYPE) if t.value.startswith(V))

    def names(self, node: object) -> list[str]:
        """Names are sorted by word count, length, then spelling."""
        found = {n.value for n in self.data.objects(node, TERMS["name"])}
        return sorted(found, key=lambda n: (-len(n.split()), -len(n), n))

    def name(self, node: object) -> str:
        names = self.names(node)
        return names[0] if names else pretty_iri(node.value)

    def labels(self, node: object) -> list[str]:
        labels = [CONFIG["type_labels"].get(t, spell_out(t)) for t in self.types(node)]
        if not labels:
            for predicate, label in CONFIG["untyped_labels"].items():
                if self.data.subjects(v(predicate), node):
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
            return "/" + self.paths[node]
        if isinstance(node, ox.NamedNode) and not node.value.startswith(MERGED):
            return node.value
        return None

    def link(self, node: object, label: str | None = None) -> str:
        shown = html.escape(label or self.name(node))
        target = self.href(node)
        if not target:
            return shown
        css = "" if target.startswith("/") else ' class="external"'
        return f'<a href="{html.escape(target)}"{css}>{shown}</a>'

    def kind_link(self, predicate: ox.NamedNode, value: str) -> str:
        return f'<a href="/{self.kind_paths[(predicate, value)]}">{html.escape(capital(value))}</a>'

    # Values.
    def literal(self, value: ox.Literal) -> str:
        if value.datatype.value in DATES:
            return format_date(value.value)
        if value.datatype.value == XSD + "integer":
            return f"{int(value.value):,}"
        return html.escape(value.value)

    def is_paper(self, node: object) -> bool:
        return bool(set(self.types(node)) & set(CONFIG["citation"]["types"]))

    def citation(self, node: object, keep: object = None) -> str:
        """A publication is shown with its authors, venue, date and identifier."""
        d, config = self.data, CONFIG["citation"]
        number = d.value(node, v(config["number"]))
        title = f"{number.value} {self.name(node)}" if number is not None else self.name(node)
        parts = [f'<span class="publication-title">{self.link(node, title)}</span>']
        head = d.value(node, v(config["authors"]))
        if head is not None and head in self.lists:
            people = d.members(head)
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
        venue = d.value(node, v(config["venue"]))
        if venue is not None:
            details.append(f"<em>{self.link(venue)}</em>")
        for predicate in config["dates"]:
            when = d.value(node, v(predicate))
            if when is not None:
                details.append(format_date(when.value))
                break
        kind = d.value(node, v(config["kind"]))
        if kind is not None and (v(config["kind"]), kind.value) in self.kind_paths:
            details.append(self.kind_link(v(config["kind"]), kind.value))
        identifier = d.value(node, v(config["identifier"]))
        if identifier is not None and isinstance(node, ox.NamedNode) and not node.value.startswith(MERGED):
            details.append(f'<a class="external" href="{html.escape(node.value)}">{html.escape(identifier.value)}</a>')
        if details:
            parts.append(f'<span class="publication-details">{" · ".join(details)}</span>')
        return "".join(parts)

    def value(self, node: object, around: object = None) -> str:
        if isinstance(node, ox.Literal):
            return self.literal(node)
        if node in self.lists:
            return ", ".join(self.value(item) for item in self.data.members(node))
        if isinstance(node, ox.BlankNode):
            return self.summary(node, around)
        if node == around:  # for example a repository that is also the identifier of the software
            return f'<a class="external" href="{html.escape(node.value)}">{html.escape(pretty_iri(node.value))}</a>'
        if self.is_paper(node):
            return self.citation(node)
        return self.link(node)

    def dates(self, node: object) -> str:
        d, config = self.data, CONFIG["dates"]
        start, end = d.value(node, v(config["start"])), d.value(node, v(config["end"]))
        if start is not None:
            a = start.value.split("-")
            if end is not None and end.value != start.value:
                b = end.value.split("-")
                if len(a) == len(b) == 3 and a[:2] == b[:2]:
                    return fill(config["range_in_month"], month=config["months"][int(a[1]) - 1], start=int(a[2]), end=int(b[2]), year=a[0])
                return fill(config["range"], start=format_date(start.value), end=format_date(end.value))
            if set(self.types(node)) & set(config["open_ended_types"]):
                return fill(config["range"], start=format_date(start.value), end=config["open_ended"])
            return format_date(start.value)
        for predicate in config["single"]:
            when = d.value(node, v(predicate))
            if when is not None:
                return format_date(when.value)
        return ""

    def summary(self, node: ox.BlankNode, around: object = None, hide: tuple[object, ...] = ()) -> str:
        """An unnamed node (a role, grant, counter, identifier) in one line. Things in `hide`
        are not linked."""
        d, config = self.data, CONFIG["summary"]
        types = set(self.types(node))
        counter, identifier = config["counter"], config["identifier"]
        if counter["type"] in types:
            count, name = d.value(node, v(counter["count"])), d.value(node, TERMS["name"])
            shown = f"{int(count.value):,} {name.value if name else ''}".strip()
            source = d.value(node, v(counter["source"]))
            return f'<a class="external" href="{html.escape(source.value)}">{html.escape(shown)}</a>' if source else html.escape(shown)
        if identifier["type"] in types:
            return html.escape(f"{d.value(node, v(identifier['scheme'])).value}: {d.value(node, v(identifier['value'])).value}")
        parts = []
        for predicate in config["headline"]:
            headline = sorted(o.value for o in d.objects(node, v(predicate)) if isinstance(o, ox.Literal))
            if headline:
                parts.append(html.escape(", ".join(headline)))
                break
        skip = {v(p) for p in config["skip"]} | {TYPE}
        papers = []
        for q in sorted(d.out(node), key=lambda q: (q.predicate.value, str(q.object))):
            o = q.object
            if o != around and o not in hide and q.predicate not in skip and isinstance(o, ox.NamedNode) and self.href(o):
                if self.is_paper(o):
                    papers.append(f'<span class="concept-citation">{self.citation(o)}</span>')
                else:
                    parts.append(self.link(o))
        when = self.dates(node)
        if when:
            parts.append(when)
        line = " · ".join(parts)
        # A node can have several notes (the status and the paragraphs of a role), in no order in
        # the graph; the shortest is the one that reads as a note of one line.
        notes = sorted((n.value.strip() for n in d.objects(node, v(config["note"]))), key=lambda n: (len(n), n))
        if notes:
            line += f'<span class="concept-note">{html.escape(capital(notes[0]))}</span>'
        return line + "".join(papers)

    @staticmethod
    def join(lead: str, detail: str) -> str:
        """A lead and its detail. The dot separator is only put before text in the line."""
        if not detail:
            return lead
        return lead + ("" if detail.startswith('<span class="concept-note">') else " · ") + detail

    # Statements about a concept.
    def involves_me(self, node: object) -> bool:
        d = self.data
        return node == self.me or isinstance(node, ox.BlankNode) and (
            any(q.object == self.me for q in d.out(node)) or any(q.subject == self.me for q in d.into(node))
        )

    def brief(self, node: object) -> str:
        when = self.dates(node)
        return self.link(node) + (f' <span class="concept-meta">{when}</span>' if when else "")

    def outgoing(self, node: ox.NamedNode) -> dict[str, list[Leaf]]:
        d, rows, ordered = self.data, defaultdict(list), set()
        dates = CONFIG["dates"]
        hidden = {v(p) for p in CONFIG["header"]} | {v(dates["start"]), v(dates["end"])}
        for q in d.out(node):
            p, o = q.predicate, q.object
            if not p.value.startswith(V) or p in hidden or o not in self.lists and self.involves_me(o):
                continue
            if p in self.kind_predicates:
                rows[CONFIG["kinds"]["label"]].append((self.kind_link(p, o.value), None))
                continue
            name = local(p)
            label = CONFIG["forward"].get(name, spell_out(name))
            for kind, special in CONFIG["forward_by_object_type"].get(name, {}).items():
                if kind in self.types(o):
                    label = special
            if o in self.lists:
                ordered.add(label)
                rows[label] += [(self.brief(m), m) for m in d.members(o) if m != self.me]
            elif o in self.paths and o != node:
                rows[label].append((self.brief(o), o))
            else:
                inside = next((x.object for x in d.out(o) if x.object in self.paths and x.object != node), None) if isinstance(o, ox.BlankNode) else None
                rows[label].append((capital(self.value(o, around=node)), inside))
        if d.value(node, v(dates["start"])) is not None:
            rows[dates["label"]].append((self.dates(node), None))
        for label in rows.keys() - ordered:
            rows[label].sort(key=lambda x: newest_first(x[0]))
        return rows

    def reverse_label(self, predicate: ox.NamedNode, subject: object) -> str:
        name = local(predicate)
        for kind, special in CONFIG["reverse_by_subject_type"].get(name, {}).items():
            if kind in self.types(subject):
                return special
        return CONFIG["reverse"].get(name) or fill(CONFIG["reverse_default"], label=spell_out(name))

    def incoming(self, node: ox.NamedNode) -> dict[str, list[Leaf]]:
        d, rows = self.data, defaultdict(list)
        skip = {v(p) for p in CONFIG["reverse_skip"]}
        for q in d.into(node):
            s, p = q.subject, q.predicate
            in_list = p == FIRST and s in self.lists
            if not in_list and (not p.value.startswith(V) or p in skip) or s == node or self.involves_me(s):
                continue
            if in_list:  # a member of an ordered list (for example authors): the owner of the list
                head = s
                while (found := next(iter(d.subjects(REST, head)), None)) is not None:
                    head = found
                for owner in d.into(head):
                    rows[self.reverse_label(owner.predicate, owner.subject)].append((self.brief(owner.subject), owner.subject))
            elif isinstance(s, ox.BlankNode):  # a role, grant or instance: what it belongs to
                anchor = self.anchor(s, node)
                if anchor != self.me:
                    detail = self.summary(s, around=node, hide=(self.me,))
                    rows[self.reverse_label(p, s)].append((detail if anchor is None else self.join(self.link(anchor), detail), anchor))
            elif not s.value.startswith(BASE):
                rows[self.reverse_label(p, s)].append((self.brief(s), s))
        for leaves in rows.values():
            leaves.sort(key=lambda x: newest_first(x[0]))
        return rows

    def anchor(self, node: ox.BlankNode, around: object, seen: frozenset = frozenset()) -> object:
        """The nearest named resource is used as the link target."""
        d = self.data
        owners = sorted((q.subject for q in d.into(node) if q.subject not in self.lists), key=str)
        for owner in owners:
            if not isinstance(owner, ox.BlankNode) and owner != self.me:
                return owner
        for owner in owners:
            if isinstance(owner, ox.BlankNode) and owner not in seen:
                for q in d.out(owner):
                    if q.predicate != TYPE and q.object in self.paths and q.object != around:
                        return q.object
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

    def neighbors(self, node: object) -> set[Node]:
        """The concepts that a node is connected to, directly or through unnamed nodes (roles, lists)."""
        if node not in self._neighbors:
            found, seen, frontier = set(), {node}, [node]
            while frontier:
                current = frontier.pop()
                for other in [q.object for q in self.data.out(current)] + [q.subject for q in self.data.into(current)]:
                    if other in seen:
                        continue
                    seen.add(other)
                    if other in self.paths:
                        found.add(other)
                    elif isinstance(other, ox.BlankNode):
                        frontier.append(other)
            self._neighbors[node] = found
        return self._neighbors[node]

    def direct(self, node: object) -> set[Node]:
        if node not in self._direct:
            d, found, seen, frontier = self.data, set(), {node}, [(node, "")]
            while frontier:
                current, way = frontier.pop()
                steps = [(q.object, "down") for q in d.out(current)] if way != "up" else []
                steps += [(q.subject, "up") for q in d.into(current)] if way != "down" else []
                for other, direction in steps:
                    if other in seen:
                        continue
                    seen.add(other)
                    if other in self.paths:
                        found.add(other)
                    elif isinstance(other, ox.BlankNode):
                        frontier.append((other, direction if other in self.lists else ""))
            self._direct[node] = found
        return self._direct[node]

    def year(self, node: object) -> str:
        years = re.findall(r"\b\d{4}\b", re.sub("<[^>]+>", "", self.dates(node)))
        return years[0] if years else ""

    def members(self, predicate: ox.NamedNode, value: str) -> list[Node]:
        return [q.subject for q in self.data.dataset.quads_for_predicate(predicate) if q.object.value == value]

    def identity(self, node: ox.NamedNode) -> list[dict[str, str]]:
        d, found, seen = self.data, [], set()
        title_of, provenance = ox.NamedNode(CONFIG["link_title"]), ox.NamedNode(CONFIG["provenance"])
        targets = [node, *sorted(d.objects(node, TERMS["same_as"]), key=str), *sorted(d.objects(node, TERMS["url"]), key=str)]
        for target in targets:
            if not isinstance(target, ox.NamedNode) or target.value.startswith(MERGED):
                continue
            parsed = urlparse(target.value.lower())
            key = parsed.netloc.removeprefix("www.") + parsed.path.rstrip("/") + parsed.query
            if key in seen:
                continue
            seen.add(key)
            host, title, source = urlparse(target.value).netloc, d.value(target, title_of), d.value(target, provenance)
            label = title.value if title is not None else CONFIG["hosts"].get(host) or host.removeprefix("www.") or CONFIG["host_default"]
            via = CONFIG["hosts"].get(urlparse(source.value).netloc, pretty_iri(source.value)) if source is not None else ""
            found.append({"url": target.value, "host": host, "label": label, "via": via if via != label else ""})
        return found

    def identity_html(self, node: ox.NamedNode) -> list[str]:
        config, links = CONFIG["identity"], self.identity(node)
        shown, more = links, []
        if len(links) > config["inline"]:
            first = (next((x for x in links[1:] if x["host"].removeprefix("www.") == host), None) for host in config["first"])
            shown = [links[0], *filter(None, first)]
            more = sorted((x for x in links if x not in shown), key=lambda x: x["label"].casefold())

        def anchor(x: dict[str, str], label: str, via: str = "") -> str:
            hint = f' title="{html.escape(via)}"' if via else ""
            return f'<a class="external" href="{html.escape(x["url"])}"{hint}>{html.escape(label)}</a>'

        via = {x["url"]: text("via", source=x["via"]) if x["via"] else "" for x in links}
        inline = [anchor(x, CONFIG["hosts"].get(x["host"], x["label"]), via[x["url"]]) for x in shown]
        label = CONFIG["identifier_label"]
        inline += [
            f"{label} {html.escape(i.value)}" for i in self.data.objects(node, TERMS["identifier"])
            if isinstance(i, ox.Literal) and not any(x["label"] == label for x in links)
        ]
        if not more:
            return ['<p class="concept-identity">' + " · ".join(inline) + "</p>"] if inline else []
        button = f'<button type="button" class="contact-more screen-only" popovertarget="more-links">{html.escape(text("more", count=len(more)))}</button>'
        items = "".join(f'<li>{anchor(x, x["label"])}<span class="more-links-via">{html.escape(via[x["url"]])}</span></li>' for x in more)
        return [
            '<p class="concept-identity">' + " · ".join([*inline, button]) + "</p>",
            f'<div id="more-links" class="more-links screen-only" popover><div class="more-links-head"><p>{html.escape(text("also_on"))}</p>'
            f'<button type="button" popovertarget="more-links" popovertargetaction="hide" aria-label="{html.escape(text("close"))}">×</button></div>'
            f"<ul>{items}</ul></div>",
        ]

    # Statements for the page files.
    def statements(self, node: ox.NamedNode) -> list[ox.Quad]:
        """The statements of the concept and those to it, with the unnamed nodes between."""
        d, out, seen = self.data, [], set()

        def closure(n: object) -> None:
            if n in seen:
                return
            seen.add(n)
            for q in d.out(n):
                out.append(q)
                if isinstance(q.object, ox.BlankNode):
                    closure(q.object)

        closure(node)
        for q in d.into(node):
            out.append(q)
            if isinstance(q.subject, ox.BlankNode):
                closure(q.subject)
                out.extend(d.into(q.subject))
        return out

    def listing(self, members: Sequence[Node], predicate: ox.NamedNode | None = None) -> list[ox.Quad]:
        """The types and names (and kind) of the members, for the files of a category or kind."""
        wanted = {TYPE, TERMS["name"], *([predicate] if predicate else [])}
        return [q for member in members for q in self.data.out(member) if q.predicate in wanted]


def published(statements: list[ox.Quad]) -> Graph:
    """Statements for the page files. Merged unnamed nodes are blank nodes again: nothing is minted."""
    blank: dict[str, ox.BlankNode] = {}

    def back(term: Node) -> Node:
        if isinstance(term, ox.NamedNode) and term.value.startswith(MERGED):
            return blank.setdefault(term.value, ox.BlankNode())
        return term

    triples = {ox.Triple(back(q.subject), q.predicate, back(q.object)) for q in statements}
    graph = Graph().parse(data=ox.serialize(triples, format=ox.RdfFormat.N_TRIPLES).decode(), format="nt")
    graph.bind(CONFIG["vocabulary_prefix"], V)
    return graph


def document(concepts: Concepts, statements: list[ox.Quad], page: str, name: str, about: object) -> Graph:
    """The visible statements, page subject and author are published together."""
    config, node = CONFIG["signposting"], ox.NamedNode(page)
    extra = [ox.Quad(node, TYPE, v(kind)) for kind in config["page_types"]]
    extra.append(ox.Quad(node, TERMS["name"], ox.Literal(name, language="en")))
    if isinstance(about, ox.NamedNode) and not about.value.startswith(MERGED):
        extra.append(ox.Quad(node, v(config["about"]), about))
    if isinstance(concepts.me, ox.NamedNode):
        extra.append(ox.Quad(node, v(config["author"]), concepts.me))
    return published([*statements, *extra])


def signposts(concepts: Concepts, about: object) -> list[str]:
    """FAIR Signposting in the head: each format, the types of the page and of its subject, the
    author, and a persistent identifier to cite, when the subject has one."""
    config = CONFIG["signposting"]
    links = [
        f'<link rel="describedby alternate" type="{f["type"]}" href="{f["file"]}" title="{html.escape(f["name"])}" />'
        for f in CONFIG["formats"]
    ]
    types = [V + t for t in config["page_types"]]
    if about is not None:
        types += [V + t for t in concepts.types(about)]
    links += [f'<link rel="type" href="{html.escape(t)}" />' for t in dict.fromkeys(types)]
    if isinstance(concepts.me, ox.NamedNode):
        links.append(f'<link rel="author" href="{html.escape(concepts.me.value)}" />')
    if isinstance(about, ox.NamedNode) and urlparse(about.value).netloc in config["persistent_hosts"]:
        links.append(f'<link rel="cite-as" href="{html.escape(about.value)}" />')
    return links


def frame(
    shell: str, concepts: Concepts, name: str, summary: str, page: str, body: list[str],
    statements: list[ox.Quad], about: object = None, current: str | None = None, scripts: Sequence[str] = (),
) -> tuple[str, Graph]:
    """The page and its graph are inserted into the shared layout."""
    doc = document(concepts, statements, page, name, about)
    embedded = doc.serialize(format="json-ld", context={"@vocab": V, CONFIG["vocabulary_prefix"]: V})
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
    schema = f'<a href="/{CONFIG["schema"]["folder"]}/">{html.escape(CONFIG["schema"]["title"])}</a>'
    page_html = (
        shell.replace("<!--concept:head-->", head)
        .replace("<!--concept:body-->", "\n".join(body))
        .replace("<!--concept:footer-->", text("footer", formats=formats, schema=schema))
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


def predicates(path: PropertyPath) -> list[str]:
    """Properties used in a path."""
    return [path.iri] if path.iri else [iri for item in path.items for iri in predicates(item)]


class Story:
    """Connections are described by schema-checked SPARQL paths."""

    def __init__(self, concepts: Concepts, schema: MinedSchema):
        config = CONFIG["story"]
        known = set(schema.get_properties()) | {RDF + "first", RDF + "rest"}
        self.concepts = concepts
        self.lines: dict[str, list[tuple[str, str, str | None, list[str]]]] = {}
        for folder, entries in config["paths"].items():
            self.lines[folder] = []
            for entry in entries:
                to = PropertyPath.from_sparql(entry["to"], config["prefixes"])
                then = PropertyPath.from_sparql(entry["then"], config["prefixes"]) if "then" in entry else None
                unknown = {iri for path in (to, then) if path for iri in predicates(path)} - known
                if unknown:
                    raise ValueError(f"The story path {entry['label']!r} of {folder} uses properties that the site does not have: {sorted(unknown)}")
                self.lines[folder].append((entry["label"], path_to_sparql(to), path_to_sparql(then) if then else None, entry.get("say", [])))
        self.store = ox.Store()
        self.store.extend(concepts.data.dataset)

    def through(self, node: ox.NamedNode, to: str, then: str) -> list[Node]:
        """The things between the owner and the concept on one path."""
        me, it = self.concepts.me.value, node.value
        query = f"SELECT DISTINCT ?via WHERE {{ <{me}> {to} ?via . ?via {then} <{it}> . FILTER(?via != <{it}> && ?via != <{me}>) }}"
        return [row["via"] for row in self.store.query(query)]

    def reaches(self, node: ox.NamedNode, to: str) -> bool:
        return bool(self.store.query(f"ASK {{ <{self.concepts.me.value}> {to} <{node.value}> }}"))

    def branches(self, node: ox.NamedNode) -> list[tuple[str, list[Leaf]]]:
        found = []
        for label, to, then, _ in self.lines.get(self.concepts.category(node)["folder"], []):
            if then is None:
                if self.reaches(node, to):
                    found.append((label, []))
                continue
            leaves = sorted({(self.leaf(via, node), via if via in self.concepts.paths else None) for via in self.through(node, to, then)}, key=lambda x: newest_first(x[0]))
            if leaves:
                found.append((label, leaves))
        return found

    def narrative(self, node: ox.NamedNode) -> str:
        concepts, d, said = self.concepts, self.concepts.data, []
        for _, to, then, say in self.lines.get(concepts.category(node)["folder"], []):
            vias = set(self.through(node, to, then)) if then and say else set()
            if not vias:
                continue
            named = sorted((x for x in vias if x in concepts.paths), key=lambda x: newest_first(concepts.brief(x)))
            roles = sorted({o.value for x in vias - set(named) for p in CONFIG["summary"]["headline"] for o in d.objects(x, v(p)) if isinstance(o, ox.Literal)})
            things = [concepts.link(x) for x in named] + [html.escape(r) for r in roles]
            subjects = Counter(t for x in named for t in d.objects(x, v(CONFIG["story"]["topics"])) if t in concepts.paths)
            topics = [concepts.link(t) for t, _ in sorted(subjects.items(), key=lambda x: (-x[1], concepts.name(x[0]).casefold()))[:3]]
            said.append(fill(
                say[0 if len(vias) == 1 else 1],
                name=html.escape(concepts.name(node)),
                things=together(things[:4] + ([html.escape(text("others", count=len(things) - 4))] if len(things) > 4 else [])),
                count=len(vias),
                topics=text("topics", names=together(topics)) if topics else "",
            ))
        return " ".join(said)

    def leaf(self, via: Node, node: ox.NamedNode) -> str:
        """A thing between: a role with its name and dates, or a named thing with its dates."""
        if isinstance(via, ox.BlankNode):  # a role: what it was, not who had it
            return capital(self.concepts.summary(via, around=node, hide=(self.concepts.me,)))
        return self.concepts.brief(via)


def together(items: list[str]) -> str:
    return items[0] if len(items) == 1 else text("together", items=", ".join(items[:-1]), last=items[-1])


def tree(concepts: Concepts, node: ox.NamedNode, story: list[tuple[str, list[Leaf]]], rows: dict[str, list[Leaf]], narrative: str = "") -> str:
    config, order = CONFIG["tree"], CONFIG["order"]
    branches = [(label, list(leaves), "tree-mine") for label, leaves in story]
    shown = {target: leaves for _, leaves, _ in branches for _, target in leaves if target is not None}
    for label in sorted(rows, key=lambda x: (order.index(x) if x in order else len(order), x.casefold())):
        kept = []
        for leaf in dict.fromkeys(rows[label]):
            if leaf[1] in shown:
                leaves = shown[leaf[1]]
                i = next(i for i, x in enumerate(leaves) if x[1] == leaf[1])
                leaves[i] = max(leaves[i], leaf, key=lambda x: len(x[0]))
                continue
            kept.append(leaf)
            if leaf[1] is not None:
                shown[leaf[1]] = kept
        if kept:
            branches.append((label, kept, ""))
    if not branches and not narrative:
        return ""
    near = concepts.direct(node)

    def item(leaf: Leaf) -> str:
        found = sorted(near & concepts.direct(leaf[1]) - {node, leaf[1]}, key=lambda n: concepts.name(n).casefold()) if leaf[1] is not None else []
        if len(found) < config["shared"]:
            return f"<li>{leaf[0]}</li>"
        names = [concepts.link(n) for n in found[:2]] + ([html.escape(text("others", count=len(found) - 2))] if len(found) > 2 else [])
        return f'<li>{leaf[0]} <span class="tree-through">{text("through", names=together(names))}</span></li>'

    def note(leaves: list[Leaf]) -> str:
        targets = [t for _, t in leaves if t is not None]
        years = sorted({y for t in targets if (y := concepts.year(t))})
        kinds = {concepts.category(t)["folder"] for t in targets}
        parts = []
        if len(leaves) > 1 and len(kinds) == 1 and len(targets) == len(leaves):
            category = concepts.category(targets[0])
            parts.append(f'{len(leaves)} {category.get("many", category["title"].lower())}')
        if len(years) > 1:
            parts.append(f"{years[0]}–{years[-1]}")
        return f' <span class="tree-note">{html.escape(", ".join(parts))}</span>' if parts else ""

    flags = [html.escape(label) for label, leaves, _ in branches if not leaves]
    items = [f'<li class="tree-branch tree-mine"><p class="tree-label">{" · ".join(flags)}</p></li>'] if flags else []
    for label, leaves, css in branches:
        if not leaves:
            continue
        body = "".join(item(leaf) for leaf in leaves[: config["limit"]])
        if len(leaves) > config["limit"]:
            rest = "".join(item(leaf) for leaf in leaves[config["limit"] :])
            body += f'<li class="tree-rest"><details><summary>{html.escape(text("more_leaves", count=len(leaves) - config["limit"]))}</summary><ul>{rest}</ul></details></li>'
        items.append(f'<li class="tree-branch {css}"><p class="tree-label">{html.escape(label)}{note(leaves)}</p><ul class="tree-leaves">{body}</ul></li>')
    closest = []
    if concepts.category(node)["folder"] in config["closest_in"]:
        ranked = sorted(((len(near & concepts.direct(n)), n) for n in concepts.nodes if n != node), key=lambda x: (-x[0], concepts.name(x[1]).casefold()))
        closest = [concepts.link(n) for count, n in ranked[:3] if count >= config["closest"]]
    lede = " ".join(x for x in (narrative, text("closest", name=html.escape(concepts.name(node)), names=together(closest)) if closest else "") if x)
    lede = f'<p class="tree-summary">{lede}</p>' if lede else ""
    return (
        f'<section class="cv-section" aria-labelledby="connections"><h2 id="connections">{html.escape(text("connections"))}</h2>'
        f'{lede}<ul class="concept-tree">{"".join(items)}</ul></section>'
    )


def render(concepts: Concepts, node: ox.NamedNode, shell: str, story: Story) -> tuple[str, Graph]:
    d = concepts.data
    name, labels = concepts.name(node), concepts.labels(node)
    description = d.value(node, TERMS["description"])
    alternate = sorted(a.value for a in d.objects(node, TERMS["alternate_name"]))
    extra = []
    if alternate:
        extra.append(f'<p class="concept-alternate">{html.escape(text("also_known_as", names=", ".join(alternate)))}</p>')
    listed = concepts.names(node)[1:]
    if listed:
        extra.append(f'<p class="concept-alternate">{html.escape(text("also_listed_as", names=", ".join(listed)))}</p>')
    if description is not None:
        shown = html.escape(description.value)
        for target in sorted({o.value for o in d.objects(node, v("mentions"))}, key=len, reverse=True):  # a mentioned link in the text
            shown = shown.replace(html.escape(target), f'<a href="{html.escape(target)}">{html.escape(target)}</a>', 1)
        extra.append(f'<p class="concept-description">{shown}</p>')
    extra += concepts.identity_html(node)
    kicker = f'<a href="/{concepts.category(node)["folder"]}/">{html.escape(" · ".join(labels))}</a>'
    rows: dict[str, list[Leaf]] = defaultdict(list)
    for part in (concepts.outgoing(node), concepts.incoming(node)):
        for label, leaves in part.items():
            rows[label] += leaves
    facts = {label: [x for x, _ in leaves] for label, leaves in rows.items() if all(t is None for _, t in leaves)}
    links = {label: leaves for label, leaves in rows.items() if label not in facts}
    body = [*header(kicker, name, *extra), section(text("details"), facts), tree(concepts, node, story.branches(node), links, story.narrative(node)), "</div>"]
    summary = description.value if description is not None else text("summary", kind=labels[0], owner=concepts.owner)
    return frame(shell, concepts, name, summary, BASE + concepts.paths[node], body, concepts.statements(node), node)


def render_kind(concepts: Concepts, predicate: ox.NamedNode, value: str, shell: str) -> tuple[str, Graph]:
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


def render_category(concepts: Concepts, category: dict[str, Any], members: list[ox.NamedNode], shell: str) -> tuple[str, Graph]:
    """Category members are grouped by kind or label."""
    d, title = concepts.data, category["title"]
    if category.get("lists"):
        items = []
        for member in members:
            here = sorted({concepts.entry(s) for s in d.subjects(v(category["lists"]), member) if s in concepts.paths}, key=newest_first)
            listed = "<ul>" + "".join(f"<li>{h}</li>" for h in here) + "</ul>" if here else ""
            items.append(f"<dt>{concepts.link(member)}</dt><dd>{listed}</dd>")
        content = '<section class="cv-section"><dl class="facts">' + "".join(items) + "</dl></section>"
    else:
        rows: dict[str, list[str]] = defaultdict(list)
        for member in members:
            kind = min((o.value for p in concepts.kind_predicates for o in d.objects(member, p)), default=None)
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


def render_topics(concepts: Concepts, category: dict[str, Any], members: list[ox.NamedNode], shell: str) -> tuple[str, Graph]:
    """The publications under each of their categories (Wikidata main subjects and OpenAlex
    topics, with the source in small type), newest first; publications without one at the end."""
    d, config = concepts.data, CONFIG["topics"]
    about = v(CONFIG["story"]["topics"])

    def sources(topic: Node) -> list[str]:
        iris = [topic.value, *(o.value for o in d.objects(topic, TERMS["same_as"]))]
        return [s["name"] for s in config["sources"] if any(i.startswith(s["prefix"]) for i in iris)]

    def brief(pub: ox.NamedNode) -> str:
        venue = d.value(pub, v(CONFIG["citation"]["venue"]))
        kind = d.value(pub, v(CONFIG["citation"]["kind"]))
        details = [f"<em>{concepts.link(venue)}</em>" if venue is not None else "", concepts.year(pub), capital(kind.value) if kind is not None else ""]
        return f'<span class="publication-title">{concepts.link(pub)}</span><span class="publication-details">{" · ".join(x for x in details if x)}</span>'

    newest = sorted(members, key=lambda p: (-int(concepts.year(p) or 0), concepts.name(p).casefold()))
    groups: dict[Node | None, list[ox.NamedNode]] = defaultdict(list)
    for pub in newest:
        for topic in [t for t in d.objects(pub, about) if t in concepts.paths and sources(t)] or [None]:
            groups[topic].append(pub)
    blocks = []
    for topic in sorted(groups, key=lambda t: (t is None, -len(groups[t]), concepts.name(t).casefold() if t is not None else "")):
        if topic is None:
            title = html.escape(config["none"])
        else:
            title = concepts.link(topic) + "".join(f' <span class="topic-source">({html.escape(s)})</span>' for s in sources(topic))
        entries = "".join(f"<li>{brief(p)}</li>" for p in groups[topic])
        blocks.append(f'<div class="topic"><h3>{title}</h3><ul class="publication-list">{entries}</ul></div>')
    ranked = [concepts.link(t) for t in sorted((t for t in groups if t is not None), key=lambda t: (-len(groups[t]), concepts.name(t).casefold()))]
    shown, more = ranked[: config["shown"]], ranked[config["shown"] :]
    lede = fill(config["lede"], topics=together(shown) if not more else ", ".join(shown))
    if more:
        lede += f' <details class="topic-more"><summary>{html.escape(fill(config["more"], count=len(more)))}</summary>{together(more)}.</details>'
    body = [*header(html.escape(text("category")), category["title"], f'<div class="concept-description">{lede}</div>'),
            f'<section class="cv-section">{"".join(blocks)}</section>', "</div>"]
    summary = re.sub("<[^>]+>", "", lede)
    return frame(shell, concepts, category["title"], summary, BASE + category["folder"] + "/", body, concepts.listing(members))


def render_kinds(concepts: Concepts, category: dict[str, Any], shell: str) -> tuple[str, Graph]:
    """The kinds of a category (/event/kind/), each with the number of things of that kind."""
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


def render_content(concepts: Concepts, grouped: dict[str, list[ox.NamedNode]], categories: dict[str, dict[str, Any]], shell: str) -> tuple[str, Graph]:
    """Pages and concepts are listed in a searchable table."""
    config, d = CONFIG["content"], concepts.data
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
            kinds = sorted((p.value, o.value) for p in concepts.kind_predicates for o in d.objects(member, p) if (p, o.value) in concepts.kind_paths)
            kind = concepts.kind_link(ox.NamedNode(kinds[0][0]), kinds[0][1]) if kinds else html.escape(concepts.labels(member)[0])
            rows.append(row(concepts.link(member), kind, concepts.year(member), str(len(concepts.neighbors(member)))))
        total += len(rows)
        groups.append(group(categories[folder]["title"], f"/{folder}/", rows))
    lede = html.escape(fill(config["lede"], count=total, categories=len(grouped)))
    lede += f' <a href="/{CONFIG["schema"]["folder"]}/">{html.escape(config["schema_link"])}</a>'
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
    """Generated pages are replaced in the sitemap."""
    sitemap = SITE / CONFIG["files"]["sitemap"]
    existing = sitemap.read_text(encoding="utf-8")
    drop = set(urls) | set(stale)
    kept = [u for u in re.findall(r"<url>.*?</url>", existing, re.DOTALL) if re.search(r"<loc>(.*?)</loc>", u).group(1) not in drop]
    added = [f"<url><loc>{u}</loc>{lastmod}</url>" for u in urls]
    head = existing.split("<url>")[0].rstrip() if "<url>" in existing else existing.split("</urlset>")[0].rstrip()
    sitemap.write_text(head + "\n  " + "\n  ".join(kept + added) + "\n</urlset>\n", encoding="utf-8")


def write(folder: str, rendered: tuple[str, Graph]) -> None:
    """A page and its graph in each configured format, side by side."""
    page, graph = rendered
    (SITE / folder).mkdir(parents=True, exist_ok=True)
    (SITE / folder / "index.html").write_text(page, encoding="utf-8")
    for f in CONFIG["formats"]:
        (SITE / folder / f["file"]).write_text(graph.serialize(format=f["rdflib"]), encoding="utf-8")


def clear_previous() -> list[str]:
    """Previously generated pages are removed before the next build."""
    manifest = SITE / CONFIG["files"]["manifest"]
    if not manifest.exists():
        return []
    previous: list[str] = json.loads(manifest.read_text(encoding="utf-8"))
    # Deepest first, so that the folder of a category is empty when it is reached.
    for path in sorted(previous, key=lambda p: -p.count("/")):
        target = SITE / path
        for name in ("index.html", *(f["file"] for f in CONFIG["formats"])):
            (target / name).unlink(missing_ok=True)
        if target.is_dir() and not any(target.iterdir()):
            target.rmdir()
    return previous


def main() -> None:
    previous = clear_previous()
    data = Data(q for source in CONFIG["sources"] for q in ox.parse(path=SITE / source))
    concepts = Concepts(data)
    story = Story(concepts, MinedSchema.from_json(SITE / CONFIG["schema"]["folder"] / CONFIG["schema"]["schema_file"]))
    files = CONFIG["files"]
    shell = (SITE / files["shell"]).read_text(encoding="utf-8")
    index: dict[str, dict[str, str]] = {}

    for node in concepts.nodes:
        write(concepts.paths[node], render(concepts, node, shell, story))
        key = unquote(node.value.removeprefix(MERGED)) if node.value.startswith(MERGED) else node.value
        index[key] = {"path": "/" + concepts.paths[node], "name": concepts.name(node)}
    for key, node in concepts.aliases.items():
        if node in concepts.paths:
            index[key] = {"path": "/" + concepts.paths[node], "name": concepts.name(node)}
    for (predicate, value), path in concepts.kind_paths.items():
        write(path, render_kind(concepts, predicate, value, shell))
        index[f"kind:{local(predicate)}|{value}"] = {"path": "/" + path, "name": capital(value)}

    hubs = []
    grouped: dict[str, list[ox.NamedNode]] = defaultdict(list)
    categories = {c["folder"]: c for c in [*CONFIG["categories"], CONFIG["other_category"]]}
    for node in concepts.nodes:
        grouped[concepts.category(node)["folder"]].append(node)
    for folder, members in sorted(grouped.items()):
        if not categories[folder].get("existing"):
            render_page = render_topics if folder == CONFIG["topics"]["category"] else render_category
            write(f"{folder}/", render_page(concepts, categories[folder], members, shell))
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
    profile = data.subjects(TYPE, v(CONFIG["profile"]["type"]))[0]
    modified = data.value(profile, v(CONFIG["profile"]["modified"]))
    lastmod = f"<lastmod>{modified.value}</lastmod>" if modified else ""
    merge_sitemap([BASE + path for path in paths], [BASE + path for path in previous], lastmod)
    print(f"Wrote {len(concepts.nodes)} concept, {len(concepts.kind_paths)} kind, and {len(hubs)} category pages to {SITE}")


if __name__ == "__main__":
    main()
