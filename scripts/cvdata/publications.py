"""Publications from ORCID, their authors from Crossref or DataCite, and who each author is."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

import yaml
from rdfsolve.sparql_helper import EndpointError

from .config import DATA_DIR, EVENT_WORK_TYPES, ORCID_ID
from .files import write_yaml_list
from .names import compatible, fold, full_name, is_me, names_fit
from .net import Cache, fetch_json, wikidata_item, wikidata_rows


def authors(doi: str) -> list[dict]:
    """Ordered author list from Crossref, or DataCite for DOIs Crossref does not register.

    Each author has given and family names, an ORCID IRI when the metadata has one, and `me` for this CV's
    subject (matched by ORCID, else by name, since registries split family names inconsistently).
    """
    rows: list[tuple[str, str, str | None]] = []
    try:
        message = fetch_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")["message"]
        rows = [(a.get("given", ""), a.get("family") or a.get("name", ""), a.get("ORCID")) for a in message.get("author", [])]
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        try:
            creators = fetch_json(f"https://api.datacite.org/dois/{urllib.parse.quote(doi)}")["data"]["attributes"]["creators"]
            for c in creators:
                orcid = next((n["nameIdentifier"] for n in c.get("nameIdentifiers", []) if "orcid" in n.get("nameIdentifierScheme", "").lower()), None)
                rows.append((c.get("givenName", ""), c.get("familyName") or c.get("name", ""), orcid))
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            return []
    result = []
    for given, family, orcid in rows:
        if not family or family.startswith(":"):  # placeholders such as ":unav"
            continue
        orcid = orcid.replace("http://", "https://") if orcid else None
        if orcid and not orcid.startswith("https://orcid.org/"):
            orcid = f"https://orcid.org/{orcid}"
        result.append({"given": given, "family": family, "orcid": orcid, "me": "true" if is_me(given, family, orcid) else None})
    return result


def orcid_claimants(doi: str) -> list[str]:
    """The ORCID iDs whose records claim this DOI."""
    query = urllib.parse.quote(f'doi-self:"{doi}"')
    try:
        found = fetch_json(f"https://pub.orcid.org/v3.0/search/?q={query}").get("result") or []
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    return [f"https://orcid.org/{r['orcid-identifier']['path']}" for r in found]


def orcid_names(orcid: str) -> list[str]:
    """Every name an ORCID record gives: the main one and its other names."""
    try:
        details = fetch_json(f"https://pub.orcid.org/v3.0/{orcid.rsplit('/', 1)[-1]}/personal-details")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    name = details.get("name") or {}
    main = " ".join(v for v in ((name.get("given-names") or {}).get("value"), (name.get("family-name") or {}).get("value")) if v)
    others = [o["content"] for o in (details.get("other-names") or {}).get("other-name", []) if o.get("content")]
    return [n for n in [main, *others] if n]


def wikidata_authorship(doi: str) -> list[dict]:
    """The authors of a work on Wikidata, by position (P50 with P1545), each with the names of the
    author item (labels and aliases) and its ORCID (P496). The work is in the scholarly graph of
    Wikidata; the authors are in the main graph."""
    qid = wikidata_item("P356", doi.upper(), scholarly=True)
    if not qid:
        return []
    rows = wikidata_rows(
        f"SELECT ?author ?position WHERE {{ wd:{qid} p:P50 ?statement . "
        "?statement a wikibase:BestRank; ps:P50 ?author; pq:P1545 ?position }",
        scholarly=True,
    )
    positioned = {row["author"]: int(row["position"]) for row in rows if row["position"].isdigit()}
    authors = {item: {"item": item, "position": position, "names": set(), "orcid": None} for item, position in positioned.items()}
    ids = sorted(authors)
    for start in range(0, len(ids), 50):
        values = " ".join(f"<{item}>" for item in ids[start : start + 50])
        for row in wikidata_rows(
            f"SELECT ?author ?name ?orcid WHERE {{ VALUES ?author {{ {values} }} "
            "{ ?author rdfs:label|skos:altLabel ?name } UNION { ?author wdt:P496 ?orcid } }"
        ):
            author = authors[row["author"]]
            author["names"] |= {row["name"]} if "name" in row else set()
            if "orcid" in row:
                author["orcid"] = min(filter(None, [author["orcid"], f"https://orcid.org/{row['orcid']}"]))
    return [{**a, "names": sorted(a["names"])} for a in sorted(authors.values(), key=lambda a: a["item"])]


def reconcile_authors(rows: list[dict], refresh: bool = False) -> None:
    """ORCID is the source of truth for who an author is. An author listed without one is
    identified, in order, by:
    1. the ORCID record of someone who claims the work and whose name fits (only if one fits);
    2. the work's authorship on Wikidata, by position, when the name there fits: the author
       item's ORCID, or the item itself when it has none;
    3. the ORCID of the one identified co-author with the same full name (however it is split),
       or the same family name and a compatible given name.
    How each was identified is kept (`via`). Names are left as each source gives them; the shared
    identifier, not a rewritten name, says they are one person. The one exception is this CV's
    subject, whose name is split as cv.yml gives it (registries split names inconsistently)."""
    person = yaml.safe_load((DATA_DIR / "cv.yml").read_text(encoding="utf-8"))["person"]
    cache = Cache(refresh)
    me = f"https://orcid.org/{ORCID_ID}"
    counts = {"ORCID record": 0, "Wikidata": 0, "co-author": 0}

    def unidentified(row: dict) -> list[dict]:
        return [a for a in row.get("authors") or [] if not a.get("orcid") and not a.get("wikidata") and not a.get("me")]

    for row in rows:
        for author in row.get("authors") or []:
            if author.get("me") and not author.get("orcid"):
                author["orcid"] = me
        if not row.get("doi") or not unidentified(row):
            continue
        claimed = {a.get("orcid") for a in row["authors"]}
        for orcid in cache.get(f"orcid-claimants:{row['doi'].lower()}", lambda doi=row["doi"]: orcid_claimants(doi)):
            if orcid in claimed:
                continue
            names = cache.get(f"orcid-names:{orcid}", lambda orcid=orcid: orcid_names(orcid))
            fits = [a for a in unidentified(row) if any(names_fit(a.get("given", ""), a["family"], n) for n in names)]
            if len(fits) == 1:
                fits[0]["orcid"], fits[0]["via"] = orcid, "ORCID record claims this work"
                counts["ORCID record"] += 1
        if not unidentified(row):
            continue
        listed = row["authors"]
        for entry in cache.get(f"wikidata-work:{row['doi'].lower()}", lambda doi=row["doi"]: wikidata_authorship(doi)):
            index = entry["position"] - 1
            if not 0 <= index < len(listed):
                continue
            author = listed[index]
            if author.get("orcid") or author.get("wikidata") or author.get("me"):
                continue
            if not any(names_fit(author.get("given", ""), author["family"], n) for n in entry["names"]):
                continue
            if entry["orcid"] and entry["orcid"] not in {a.get("orcid") for a in listed}:
                author["orcid"] = entry["orcid"]
            author["wikidata"], author["via"] = entry["item"], "Wikidata authorship"
            counts["Wikidata"] += 1
    cache.save()

    people: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        for author in row.get("authors") or []:
            if author.get("orcid"):
                people.setdefault(author["orcid"], []).append((author.get("given", ""), author["family"]))
    for row in rows:
        for author in unidentified(row):
            family, given = fold(author["family"]), author.get("given", "")
            full = full_name(given, author["family"])
            fits = [
                orcid for orcid, names in people.items()
                if any(full_name(g, f) == full or (fold(f) == family and compatible(given, g)) for g, f in names)
            ]
            if len(fits) == 1:
                author["orcid"], author["via"] = fits[0], "same name as an identified co-author"
                counts["co-author"] += 1
    for row in rows:
        for author in row.get("authors") or []:
            author.setdefault("me", None)
            if author["me"] or (author.get("orcid") or "").endswith(ORCID_ID):
                author["given"], author["family"] = person["given_name"], person["family_name"]
            author["me"] = "true" if (author.get("orcid") or "").endswith(ORCID_ID) else author["me"]
    print(f"authors: {len(people)} people identified by ORCID; newly identified: " + ", ".join(f"{n} via {k}" for k, n in counts.items()))


def add_subjects(rows: list[dict]) -> None:
    dois = " ".join(json.dumps(r["doi"].upper()) for r in rows if r.get("doi"))
    query = f"""SELECT ?doi ?subject ?name WHERE {{
  VALUES ?doi {{ {dois} }}
  ?work wdt:P356 ?doi; wdt:P921 ?subject .
  ?subject rdfs:label ?name FILTER(LANG(?name) = "en")
}}"""
    found: dict[str, dict[str, str]] = defaultdict(dict)
    for scholarly in (False, True):
        try:
            for r in wikidata_rows(query, scholarly=scholarly):
                found[r["doi"]][r["subject"]] = r["name"]
        except EndpointError:
            continue
    for row in rows:
        listed = found.get((row.get("doi") or "").upper())
        if listed:
            row["subjects"] = [{"iri": iri, "name": name} for iri, name in sorted(listed.items(), key=lambda x: x[1].casefold())]


def update_publications(works: dict) -> None:
    rows = []
    for group in works.get("group", []):
        summary = group["work-summary"][0]
        title = (summary.get("title") or {}).get("title", {})
        title = title.get("value") if title else None
        if not title:
            continue
        pub_date = summary.get("publication-date") or {}
        year = (pub_date.get("year") or {}).get("value")
        journal = summary.get("journal-title")
        journal = journal.get("value") if journal else None
        work_type = summary.get("type")
        if work_type in EVENT_WORK_TYPES:
            continue
        ext_ids = (summary.get("external-ids") or {}).get("external-id", [])
        doi = next(
            (e["external-id-value"] for e in ext_ids if e.get("external-id-type") == "doi"),
            None,
        )
        url = f"https://doi.org/{doi}" if doi else None
        rows.append(
            {
                "title": title,
                "year": year,
                "journal": journal,
                "type": work_type,
                "doi": doi,
                "url": url,
                "authors": authors(doi) if doi else None,
            }
        )
    orcid_count = len(rows)
    known_dois = {r["doi"] for r in rows if r.get("doi")}
    rows.extend(
        {
            **extra,
            "url": f"https://doi.org/{extra['doi']}",
            "authors": authors(extra["doi"]),
        }
        for extra in yaml.safe_load((DATA_DIR / "extra_publications.yml").read_text(encoding="utf-8")) or []
        if extra["doi"] not in known_dois
    )

    rows.sort(key=lambda r: r.get("year") or "0", reverse=True)
    reconcile_authors(rows)
    add_subjects(rows)
    write_yaml_list(DATA_DIR / "publications.yml", rows)
    print(f"publications.yml: {len(rows)} works ({orcid_count} from ORCID, {len(rows) - orcid_count} manually tracked)")
