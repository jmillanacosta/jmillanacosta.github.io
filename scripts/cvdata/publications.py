"""Publications and author identities are collected from public records."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import wikidata
from .config import DATA_DIR, EVENT_WORK_TYPES, ORCID_ID, TOPIC_SCORE
from .files import write_yaml_list
from .names import compatible, fold, full_name, is_me, names_fit
from .net import Cache, fetch_json, fetch_optional


def authors(doi: str) -> list[dict]:
    """Author order and names are kept as published by Crossref or DataCite."""
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
    """The authors of a work in Wikidata with their positions (author statements with a series
    ordinal, best rank only), their names in all languages and their ORCID."""
    iri, scholarly = wikidata.works([doi]).get(doi.upper(), (None, False))
    work = wikidata.read([iri], scholarly=scholarly).get(iri) if iri else None
    if work is None:
        return []
    authors = {}
    for statement in wikidata.read(work.author_statement, "Statement", scholarly=scholarly).values():
        if str(wikidata.WIKIBASE.BestRank) not in statement.rdf_type:
            continue
        for author in statement.author:
            for position in statement.series_ordinal:
                if str(position).isdigit():
                    authors[str(author)] = {"item": str(author), "position": int(position), "names": [], "orcid": None}
    for iri, person in wikidata.read(authors, languages=()).items():
        authors[iri]["names"] = sorted({*map(str, person.label), *map(str, person.alt_label)})
        if person.orcid_id:
            authors[iri]["orcid"] = f"https://orcid.org/{wikidata.first(person.orcid_id)}"
    return sorted(authors.values(), key=lambda author: author["item"])


def reconcile_authors(rows: list[dict], refresh: bool = False) -> None:
    """Missing identifiers are matched by ORCID claims, Wikidata authorship, then co-author names."""
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
        for orcid in cache.get(f"orcid-claimants:{row['doi'].lower()}", orcid_claimants, row["doi"]):
            if orcid in claimed:
                continue
            names = cache.get(f"orcid-names:{orcid}", orcid_names, orcid)
            fits = [a for a in unidentified(row) if any(names_fit(a.get("given", ""), a["family"], n) for n in names)]
            if len(fits) == 1:
                fits[0]["orcid"], fits[0]["via"] = orcid, "ORCID record claims this work"
                counts["ORCID record"] += 1
        if not unidentified(row):
            continue
        listed = row["authors"]
        for entry in cache.get(f"wikidata-work:{row['doi'].lower()}", wikidata_authorship, row["doi"]):
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
    """The Wikidata item and main subjects of each work with a DOI."""
    works = wikidata.works(row["doi"] for row in rows if row.get("doi"))
    records = {}
    for scholarly in (False, True):
        records.update(wikidata.read((iri for iri, graph in works.values() if graph == scholarly), scholarly=scholarly))
    for row in rows:
        iri, _ = works.get((row.get("doi") or "").upper(), (None, False))
        if iri:
            row["wikidata"] = iri
            subjects = wikidata.names(records[iri].main_subject) if iri in records else []
            if subjects:
                row["subjects"] = subjects


def add_topics(rows: list[dict]) -> None:
    """The OpenAlex topics of each work with a DOI, from the configured score."""
    for row in rows:
        work = fetch_optional(f"https://api.openalex.org/works/doi:{row['doi']}?select=topics") if row.get("doi") else None
        topics = [{"iri": t["id"], "name": t["display_name"]} for t in (work or {}).get("topics", []) if t["score"] >= TOPIC_SCORE]
        if topics:
            row["topics"] = topics


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
    add_topics(rows)
    write_yaml_list(DATA_DIR / "publications.yml", rows)
    print(f"publications.yml: {len(rows)} works ({orcid_count} from ORCID, {len(rows) - orcid_count} manually tracked)")
