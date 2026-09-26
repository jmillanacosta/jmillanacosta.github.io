"""The linked data of each page, made from _data as records of the shapes in shapes.py.

Each function below makes records from one kind of entry in _data. Each value is checked by
rdfsolve against the published vocabularies (schema.org, FOAF, DCMI Terms). The RDFa of a page
must give the same graph. This is checked by build-rdf.py.

Run after `bundle exec jekyll build`.
"""

import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import yaml
from rdflib import XSD, Graph, Literal, URIRef
from rdflib.namespace import FOAF
from rdfsolve.api import RDFList

from shapes import CLASSES, SCHEMA, models

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site"
DATA = {path.stem: yaml.safe_load(path.read_text(encoding="utf-8")) or [] for path in (ROOT / "_data").glob("*.yml")}
CONFIG = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))
CV = DATA["cv"]
HOME = CONFIG["canonical"]
ME = CV["person"]["iri"]
CONTEXT = {
    "@vocab": "https://schema.org/",
    "schema": "https://schema.org/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "@language": "en",
}
shapes = models()


def key(url):
    parsed = urlparse(str(url).lower())
    return parsed.netloc.removeprefix("www.") + parsed.path.rstrip("/")


def things(value):
    if isinstance(value, dict):
        yield from [value] if "iri" in value else []
        for item in value.values():
            yield from things(item)
    elif isinstance(value, list):
        for item in value:
            yield from things(item)


ALIASES = {
    key(HOME): ME,
    **{key(iri): iri for iri in CV["skill_terms"].values()},
    **{key(alias): someone["id"] for someone in DATA["collaborators"] for alias in someone.get("same_as", [])},
    **{key(alias): thing["iri"] for thing in things(CV) for alias in [thing.get("url"), *thing.get("same_as", [])] if alias},
    **{key(thing["iri"]): thing["iri"] for thing in things(CV)},
}


def new(kind, uri=None, types=(), **fields):
    """A record of *kind*. Empty fields are not used. Text is trimmed and in English."""
    values = {}
    for name, value in fields.items():
        if isinstance(value, list):
            value = [trim(item) for item in value if trim(item) not in (None, "")]
        else:
            value = trim(value)
        if value not in (None, "", []):
            values[name] = value
    return shapes.create(kind, uri=uri, extra_types=list(types), language="en", **values)


def trim(value):
    return value.strip() if type(value) is str else value  # plain text, not an RDF term


def year(text):
    """A year for dcterms:date. Any literal is accepted there, so the datatype is given."""
    return Literal(str(text), datatype=XSD.gYear)


def doi(name):
    return f"https://doi.org/{name}"


def mentioned(entry):
    return [mention["url"] for mention in entry.get("mentions", []) if mention.get("url")]


def place(name):
    return new("Place", CV["places"].get(name), name=name)


def organization(key):
    if key is None:
        return None
    org = CV["organizations"][key]
    return new(
        org["type"],
        org["iri"],
        name=org["name"],
        alternateName=org.get("alternate_name"),
        sameAs=org.get("same_as", []),
        location=[place(name) for name in [org["location"], *org.get("other_locations", [])]],
        parentOrganization=organization(org.get("parent")),
    )


def grant(key):
    item = CV["grants"][key]
    return new(
        "Grant",
        item["iri"],
        name=item["name"],
        alternateName=f"{item['program']} grant {item['number']}",
        identifier=item["number"],
        url=item["url"],
        funder=organization(item["funder"]),
    )


def counters(software):
    """The counts of downloads, stars and forks of one piece of software."""
    stats = next((s for s in DATA["code_stats"] if s["id"] == software["id"]), {})
    period = stats.get("downloads_period", "").replace("-", " ")
    return [
        new(
            "InteractionCounter",
            name=kind["name"].replace("{$period}", period),
            userInteractionCount=stats[kind["id"]],
            interactionType=kind.get("type"),
            url=stats.get(kind["source"]),
        )
        for kind in DATA["stat_kinds"]
        if stats.get(kind["id"])
    ]


def software(entry):
    package = next((p for p in DATA["software"] if p["name"] == entry.get("package")), {})
    return new(
        "SoftwareSourceCode",
        entry["iri"],
        name=entry["name"],
        codeRepository=entry["repository"],
        url=entry.get("url"),
        softwareVersion=package.get("version"),
        downloadUrl=package.get("pypi_url"),
        softwareHelp=[link for link in (package.get("docs"), entry.get("docs")) if link],
        author=ME,
        description=entry["description"],
        subjectOf=[doi(name) for name in entry.get("publications", [])],
        interactionStatistic=counters(entry),
    )


def contribution(entry):
    """Software with contributions from the person. The contribution is given as a Role."""
    released = {entry.get("doi_relation", "sameAs"): doi(entry["doi"])} if "doi" in entry else {}
    return new(
        "SoftwareSourceCode",
        entry["iri"],
        name=entry["name"],
        url=entry.get("url"),
        codeRepository=entry["repository"],
        contributor=new("Role", contributor=ME, mentions=mentioned(entry), description=entry["description"]),
        subjectOf=[doi(name) for name in entry.get("publications", [])],
        interactionStatistic=counters(entry),
        **released,
    )


def community(entry):
    return new("Project", entry["iri"], name=entry["name"], url=entry["url"], contributor=ME)


def course(entry):
    program = entry.get("program")
    return new(
        "Course",
        entry.get("iri"),
        name=entry["name"],
        url=entry.get("url"),
        courseCode=entry.get("code"),
        isPartOf=new("EducationalOccupationalProgram", name=program, url=entry.get("program_url")) if program else None,
        provider=CV["organizations"][CV["teaching_provider"]]["iri"],
        hasCourseInstance=new("CourseInstance", instructor=ME, mentions=mentioned(entry), description=entry["description"]),
    )


def report(entry):
    return new(
        "Report",
        name=entry["title"],
        dcterms_title=entry["title"],
        reportNumber=entry["number"],
        datePublished=entry["date"],
        dcterms_date=year(entry["date"]),
        url=entry["url"],
        funding=grant(entry["grant"]),
        contributor=ME,
        dcterms_contributor=ME,
    )


def author(entry):
    if entry.get("me"):
        return ME
    name = f"{entry['given']} {entry['family']}" if entry.get("given") else entry["family"]
    return new("Person", entry.get("orcid") or entry.get("wikidata"), name=name)


def article(entry):
    """A publication. The authors are kept in order as an RDF list."""
    journal = entry.get("journal")
    authors = [author(a) for a in entry.get("authors", [])]
    return new(
        "ScholarlyArticle",
        doi(entry["doi"]),
        name=entry["title"],
        dcterms_title=entry["title"],
        datePublished=entry["year"],
        dcterms_date=year(entry["year"]),
        genre=entry["type"].replace("-", " ").capitalize() if entry.get("type") else None,
        identifier=entry["doi"],
        sameAs=CV["publication_same_as"].get(entry["doi"]),
        isPartOf=new("Periodical", CV["venues"].get(journal), name=journal) if journal else None,
        author=RDFList(items=authors) if authors else ME,
        dcterms_creator=ME,
    )


def event(entry):
    online = entry["location"] == "Online"
    return new(
        "Event",
        entry.get("iri"),
        name=f"{entry['name']} {entry['year']}",
        keywords=entry["type"],
        startDate=entry.get("start") or str(entry["year"]),
        endDate=entry.get("end"),
        url=entry.get("url"),
        recordedIn=entry.get("recording"),
        superEvent=entry.get("series"),
        eventAttendanceMode="https://schema.org/OnlineEventAttendanceMode" if online else None,
        location=new("VirtualLocation", name="Online") if online else place(entry["location"]),
    )


def performance(entry):
    """A talk or a poster at an event, with the slides and the funding."""
    funder = entry.get("funder")
    funding = None
    if funder:
        note = f"{entry['funding_note']} {CV['organizations'][funder]['name']}" if entry.get("funding_note") else None
        funding = new("Grant", description=note, funder=organization(funder))
    return new(
        "Role",
        roleName=entry.get("role"),
        subjectOf=doi(entry["doi"]) if entry.get("doi") else None,
        mentions=mentioned(entry),
        funding=funding,
        description=entry["summary"],
        performerIn=event(entry),
    )


def job(entry):
    return new(
        "EmployeeRole",
        roleName=[entry["role"], URIRef(entry["occupation"])],
        startDate=entry["start"],
        endDate=entry.get("end"),
        description=[entry.get("status"), *entry["description"]],
        worksFor=organization(entry["organization"]),
    )


def project(entry):
    return new("ResearchProject", entry["iri"], name=entry["name"], url=entry["url"], funding=grant(entry["grant"]))


def study(entry):
    taxa = [new("Taxon", CV["taxa"][name]["iri"], name=name, sameAs=CV["taxa"][name]["same_as"]) for name in entry.get("taxa", [])]
    return new(
        "OrganizationRole",
        roleName=entry["degree"],
        startDate=entry["start"],
        endDate=entry["end"],
        description=entry.get("description"),
        about=taxa,
        alumniOf=organization(entry["organization"]),
    )


def exchange(entry):
    return new(
        "OrganizationRole",
        roleName=entry["role"],
        startDate=entry.get("start"),
        endDate=entry.get("end"),
        alumniOf=organization(entry.get("organization")),
    )


def degree(entry):
    return new(
        "EducationalOccupationalCredential",
        name=entry["degree"],
        credentialCategory=URIRef(entry["degree_type"]),
        about=entry["field"],
        recognizedBy=CV["organizations"][entry["organization"]]["iri"],
        dateCreated=entry["end"],
    )


def credential(entry):
    return new(
        "EducationalOccupationalCredential",
        name=entry["name"],
        recognizedBy=organization(entry["recognized_by"]),
        dateCreated=entry["date"],
    )


def language(entry):
    spoken = new("Language", entry["iri"], name=entry["name"], alternateName=entry["code"], sameAs=entry["same_as"])
    return new("Role", description=entry["proficiency"], knowsLanguage=spoken)


def events(on_cv=False):
    listed = [e for e in [*DATA["events"], *DATA["events_generated"]] if not e.get("hidden")]
    return [e for e in listed if e.get("cv", True)] if on_cv else listed


def person(parts):
    """The person of the site, with the parts of the CV that are shown on the page."""
    me = CV["person"]
    links = next((c.get("links", []) for c in DATA["collaborators"] if c["id"] == ME), [])
    education = CV["education"] if "education" in parts else []
    return new(
        "Person",
        ME,
        types=[FOAF.Person],
        name=me["name"],
        foaf_name=me["name"],
        givenName=me["given_name"],
        foaf_givenName=me["given_name"],
        familyName=me["family_name"],
        foaf_familyName=me["family_name"],
        jobTitle=me["job_title"],
        description=me["description"] if "description" in parts else None,
        email=me["email"],
        foaf_mbox=f"mailto:{me['email']}",
        url=[HOME, *(link["url"] for link in links if link["kind"] == "website")],
        foaf_homepage=HOME,
        image=f"{HOME}assets/og-image.png",
        identifier=new("PropertyValue", propertyID="ORCID", value=me["orcid"], url=ME),
        address=new("PostalAddress", addressLocality=me["address"]["locality"], addressCountry=me["address"]["country"]),
        sameAs=[
            *(profile["url"] for profile in me["profiles"] if profile["url"] != ME),
            *me["same_as"],
            *(link["url"] for link in links if link["kind"] != "website"),
        ],
        worksFor=[organization(me["works_for"]), *(job(e) for e in CV["experience"] if "roles" in parts)],
        memberOf=[project(e) for e in CV["funded_projects"] if "projects" in parts],
        alumniOf=[*(study(e) for e in education), *(exchange(x) for e in education for x in e.get("exchanges", []))],
        hasCredential=[
            *(degree(e) for e in education),
            *(credential(e) for e in CV["credentials"] if "credentials" in parts),
        ],
        knowsLanguage=[language(e) for e in CV["languages"] if "languages" in parts],
        knowsAbout=[new("Thing", iri, name=name) for name, iri in CV["skill_terms"].items() if "skills" in parts],
        performerIn=[
            *(performance(e) for e in events() if "events" in parts),
            *(performance(e) for e in events(on_cv=True) if "cv-events" in parts),
        ],
    )


def profile_page(page):
    """The page: its subject, its author and its downloads."""
    today = Literal(datetime.date.today())
    downloads = [f for f in DATA["formats"] if f.get("mode") in (None, page["mode"])]
    return new(
        "ProfilePage",
        page["iri"],
        types=[FOAF.PersonalProfileDocument],
        name=page["title"],
        dcterms_title=page["title"],
        inLanguage="en",
        dateModified=today,
        dcterms_modified=today,
        mainEntity=ME,
        foaf_primaryTopic=ME,
        foaf_maker=ME,
        dcterms_creator=ME,
        encoding=[
            new("MediaObject", page["iri"] + f["url"], name=f["name"], encodingFormat=f["type"], contentUrl=page["iri"] + f["url"])
            for f in downloads
        ],
    )


def page_records(page):
    parts = page["parts"]
    records = [profile_page(page), person(parts)]
    if "communities" in parts:
        records += [community(e) for e in CV["communities"]]
    if "teaching" in parts:
        records += [course(e) for e in CV["teaching"]]
    if "software" in parts:
        records += [software(e) for e in [*CV["libraries"], *CV["personal_tools"]]]
    if "contributions" in parts:
        records += [contribution(e) for e in CV["contributions"]]
    if "publications" in parts:
        records += [report(e) for e in CV["deliverables"]]
        records += [article(e) for e in DATA["publications"]]
    return records


def links_to(kind, name):
    return shapes.fields(kind).set_index("field").links[name]


def wikidata(kind, statements, stated, own):
    fields = defaultdict(list)
    people = {someone["id"] for someone in DATA["collaborators"]}
    for statement in statements:
        literal = Literal(statement["value"], lang=statement.get("language"), datatype=None if statement.get("language") else statement.get("datatype"))
        item = None if "datatype" in statement else ALIASES.get(key(statement["value"]))
        if not own and (not item or SCHEMA + "knowsLanguage" in statement["equivalent"]):
            continue
        typed = statement.get("value_type", "").removeprefix(SCHEMA)
        node = URIRef(item) if item else new(typed, statement["value"], name=statement.get("value_label")) if typed in CLASSES else URIRef(statement["value"])
        for prop in statement["equivalent"]:
            name = shapes.field_name(kind, prop)
            if "datatype" not in statement and links_to(kind, name):
                fields[name].append(node)
            elif prop not in stated:
                fields[name].append(literal if "datatype" in statement else statement.get("value_label"))
        if not statement["equivalent"] and item in people:
            fields["knows"].append(new("Role", roleName=[statement["label"], URIRef(statement["property"])], knows=URIRef(item)))
    return fields


def collaborator_records():
    """Collaborators, with the source of each name and link."""
    people = DATA["collaborators"]
    records = []
    everything = set().union(*(page["parts"] for page in pages()))
    for someone in people:
        fields = {
            "name": [n["name"] for n in someone.get("names", [])],
            "sameAs": [*someone.get("same_as", []), *(p["url"] for p in someone.get("profiles", []))],
            "url": [w["url"] for w in someone.get("websites", [])],
        }
        if someone.get("statements"):
            given = person(everything) if someone["id"] == ME else new("Person", someone["id"], **fields)
            stated = {str(p) for p in given.to_graph().predicates(URIRef(someone["id"]))}
            extra = wikidata("Person", someone["statements"], stated, someone["id"] == ME)
            fields = {name: [*fields.get(name, []), *extra.get(name, [])] for name in {*fields, *extra}}
        if any(fields.values()):
            records.append(new("Person", someone["id"], **fields))
        for website in someone.get("websites", []):
            records.append(new("WebSite", website["url"], dcterms_source=URIRef(website["source"])))
        for profile in someone.get("profiles", []):
            records.append(new("WebPage", profile["url"], dcterms_title=profile.get("label"), dcterms_source=URIRef(profile["source"])))
        for account in someone.get("accounts", []):
            if account.get("via") in ("declared on ORCID record", "Wikidata"):
                records.append(new("WebPage", account["url"], dcterms_source=URIRef(account["source"])))
    for entry in [*CV["libraries"], *CV["contributions"], *CV["personal_tools"]]:
        contributors = [someone["id"] for someone in people if entry["id"] in someone.get("contributes", [])]
        if contributors:
            records.append(new("SoftwareSourceCode", entry["iri"], contributor=contributors))
    return records


def subject_records():
    return [
        new("ScholarlyArticle", doi(row["doi"]), about=[
            URIRef(ALIASES[key(s["iri"])]) if key(s["iri"]) in ALIASES else new("DefinedTerm", s["iri"], name=s["name"])
            for s in row["subjects"]
        ])
        for row in DATA["publications"] if row.get("subjects")
    ]


def repository_records():
    listed = {entry["id"]: entry["iri"] for entry in [*CV["libraries"], *CV["contributions"], *CV["personal_tools"]]}
    records = []
    for row in DATA["repositories"]:
        owner = next((i for i in (ALIASES.get(key(u)) for u in (row["owner"], row.get("owner_website")) if u) if i and i != listed[row["id"]]), None)
        skills = {field: [URIRef(CV["skill_terms"][name]) for name in row.get(field, [])] for field in ("programmingLanguage", "softwareRequirements", "about")}
        records.append(new("SoftwareSourceCode", listed[row["id"]], maintainer=URIRef(owner) if owner else None, **skills))
    return records


def pages():
    """The pages that give the parts of the graph in their front matter."""
    found = []
    for source in sorted([*ROOT.glob("*.html"), *ROOT.glob("[!_]*/index.html")]):
        match = re.match(r"---\n(.*?)\n---", source.read_text(encoding="utf-8"), re.S)
        matter = (yaml.safe_load(match.group(1)) if match else None) or {}
        if "graph" in matter:
            folder = source.parent.relative_to(ROOT).as_posix().strip(".")
            prefix = f"{folder}/" if folder else ""
            title = CONFIG["title"] + (f" — {matter['title']}" if matter.get("title") else "")
            found.append({"folder": prefix, "mode": folder or "site", "iri": HOME + prefix, "title": title, "parts": set(matter["graph"])})
    return found


def write(records, name, embed_in=None):
    graph = Graph()
    for record in records:
        graph += record.to_graph()
    body = graph.serialize(format="json-ld", context=CONTEXT, auto_compact=True, indent=1)
    target = SITE / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    if embed_in and (html := SITE / embed_in).exists():
        markup = re.sub(r'\s*<script type="application/ld\+json">.*?</script>', "", html.read_text(encoding="utf-8"), flags=re.S)
        script = f'<script type="application/ld+json">{json.dumps(json.loads(body), ensure_ascii=False)}</script>\n</head>'
        html.write_text(markup.replace("</head>", script, 1), encoding="utf-8")
    print(f"{name}: {len(graph)} triples")


def main():
    for page in pages():
        write(page_records(page), page["folder"] + "index.jsonld", embed_in=page["folder"] + "index.html")
    write(collaborator_records(), "graph/collaborators.jsonld")
    write(repository_records() + subject_records(), "graph/works.jsonld")


if __name__ == "__main__":
    main()
