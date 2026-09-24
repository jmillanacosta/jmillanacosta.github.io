#!/usr/bin/env python3
"""Update publication, event, software, and usage data from ORCID, Crossref, Zenodo, PyPI, ecosyste.ms, GitHub, and Docker Hub."""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

# Prefer IPv4 when both address families are available.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_preferred_getaddrinfo(*args, **kwargs):
    results = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 or results


socket.getaddrinfo = _ipv4_preferred_getaddrinfo

# Who the data is about, from _data/cv.yml: their ORCID and GitHub account.
PERSON = yaml.safe_load((Path(__file__).resolve().parent.parent / "_data" / "cv.yml").read_text(encoding="utf-8"))["person"]
ORCID_ID = str(PERSON["orcid"])
GITHUB_USER = next(p["url"] for p in PERSON["profiles"] if p["label"] == "GitHub").rstrip("/").rsplit("/", 1)[-1]
SITE_URL = yaml.safe_load((Path(__file__).resolve().parent.parent / "_config.yml").read_text(encoding="utf-8"))["url"]


# ORCID work types that are talks or posters; they feed events, not publications.
EVENT_WORK_TYPES = {"lecture-speech", "conference-poster"}
# Zenodo upload types that are talks or posters.
EVENT_RECORD_ROLES = {"presentation": "Talk", "poster": "Poster"}

DATA_DIR = Path(__file__).resolve().parent.parent / "_data"
HEADERS = {"User-Agent": f"cv-updater/1.0 ({SITE_URL})", "Accept": "application/json"}


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def yaml_str(value: str) -> str:
    """Double-quoted YAML scalar, safe for arbitrary text."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_yaml_list(path: Path, rows: list[dict]) -> None:
    lines = []
    for row in rows:
        first = True
        for key, value in row.items():
            if value is None:
                continue
            prefix = "- " if first else "  "
            if isinstance(value, list):
                lines.append(f"{prefix}{key}:")
                for item in value:
                    fields = ", ".join(f"{k}: {yaml_str(str(v))}" for k, v in item.items() if v is not None)
                    lines.append(f"    - {{ {fields} }}")
            else:
                text = str(value) if isinstance(value, int) and not isinstance(value, bool) else yaml_str(str(value))
                lines.append(f"{prefix}{key}: {text}")
            first = False
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fold(s: str) -> str:
    """Strip accents and lowercase, for tolerant name matching."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()


def _is_me(given: str, family: str, orcid: str | None) -> bool:
    """This CV's subject: by ORCID, else by name, allowing for registries that split the family
    name differently (every part of it present, and the given name's first word)."""
    if orcid:
        return orcid.rstrip("/").endswith(ORCID_ID)
    full = _fold(f"{given} {family}").split()
    wanted = _fold(PERSON["family_name"]).split()
    first = _fold(PERSON["given_name"]).split()[:1]
    return all(part in full for part in wanted) and all(part in full for part in first)


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
        result.append({"given": given, "family": family, "orcid": orcid, "me": "true" if _is_me(given, family, orcid) else None})
    return result


def _given_parts(given: str) -> list[str]:
    return [part for part in re.split(r"[\s.\-]+", _fold(given)) if part]


def _compatible(a: str, b: str) -> bool:
    """Whether two given names can be the same person's: part by part, in order, words must be
    equal and an initial must match the word's first letter ("E." and "Egon" fit "Egon L")."""
    parts_a, parts_b = _given_parts(a), _given_parts(b)
    if not parts_a or not parts_b:
        return False
    for x, y in zip(parts_a, parts_b, strict=False):
        if (len(x) == 1 or len(y) == 1) and x[0] != y[0]:
            return False
        if len(x) > 1 and len(y) > 1 and x != y:
            return False
    return True


def _full_name(given: str, family: str) -> str:
    """A name with the given/family split, hyphens, dots, and parenthesized nicknames ignored:
    registries split names differently ("Jose Emilio Labra" + "Gayo", "Jose Emilio" +
    "Labra-Gayo"), and profiles add nicknames ("Friederike (Freddie) Ehrhart")."""
    return " ".join(_given_parts(re.sub(r"\([^)]*\)", " ", f"{given} {family}")))


def orcid_name(orcid: str) -> tuple[str, str] | None:
    """The given and family names on an ORCID record, when public."""
    try:
        name = fetch_json(f"https://pub.orcid.org/v3.0/{orcid.rstrip('/').rsplit('/', 1)[-1]}/person").get("name") or {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    given = (name.get("given-names") or {}).get("value")
    family = (name.get("family-name") or {}).get("value")
    return (given, family) if given and family else None


def _names_fit(given: str, family: str, full: str) -> bool:
    """Whether a name written in one piece ("Thomas E. Exner") can be this given and family name:
    the whole name matches, or every family-name part is in it and the rest fits the given name."""
    if _full_name(given, family) == _full_name(full, ""):
        return True
    parts, surname = _given_parts(full), _given_parts(family)
    if not surname or not all(part in parts for part in surname):
        return False
    rest = [part for part in parts if part not in surname]
    return not rest or not _given_parts(given) or _compatible(given, " ".join(rest))


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
    """A work's authors on Wikidata, by position (P50 with P1545), each with the author item's
    names (label and aliases) and ORCID (P496)."""
    qid = wikidata_item("P356", doi.upper())
    if not qid:
        return []
    claims = wikidata_api({"action": "wbgetentities", "ids": qid, "props": "claims"}).get("entities", {}).get(qid, {}).get("claims", {})
    positioned = {}
    for claim in best_rank(claims.get("P50", [])):
        ordinal = [q["datavalue"]["value"] for q in claim.get("qualifiers", {}).get("P1545", []) if "datavalue" in q]
        if ordinal and ordinal[0].isdigit() and "datavalue" in claim["mainsnak"]:
            positioned[claim["mainsnak"]["datavalue"]["value"]["id"]] = int(ordinal[0])
    authors = []
    ids = sorted(positioned)
    for start in range(0, len(ids), 50):
        params = {"action": "wbgetentities", "ids": "|".join(ids[start : start + 50]), "props": "claims|labels|aliases"}
        for item, entity in wikidata_api(params).get("entities", {}).items():
            names = {v["value"] for v in entity.get("labels", {}).values()}
            names |= {a["value"] for values in entity.get("aliases", {}).values() for a in values}
            orcid = [c["mainsnak"]["datavalue"]["value"] for c in best_rank(entity.get("claims", {}).get("P496", [])) if "datavalue" in c["mainsnak"]]
            authors.append({"item": f"http://www.wikidata.org/entity/{item}", "position": positioned[item],
                            "names": sorted(names), "orcid": f"https://orcid.org/{orcid[0]}" if orcid else None})
    return authors


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
            fits = [a for a in unidentified(row) if any(_names_fit(a.get("given", ""), a["family"], n) for n in names)]
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
            if not any(_names_fit(author.get("given", ""), author["family"], n) for n in entry["names"]):
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
            family, given = _fold(author["family"]), author.get("given", "")
            full = _full_name(given, author["family"])
            fits = [
                orcid for orcid, names in people.items()
                if any(_full_name(g, f) == full or (_fold(f) == family and _compatible(given, g)) for g, f in names)
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
    write_yaml_list(DATA_DIR / "publications.yml", rows)
    print(f"publications.yml: {len(rows)} works ({orcid_count} from ORCID, {len(rows) - orcid_count} manually tracked)")


def _quoted(title: str) -> str:
    return f"\u201c{title.strip().rstrip('.')}\u201d"


def orcid_events(works: dict) -> list[dict]:
    """Talks and posters recorded on ORCID; the conference title field names the event."""
    rows = []
    for group in works.get("group", []):
        summary = group["work-summary"][0]
        if summary.get("type") not in EVENT_WORK_TYPES:
            continue
        title = ((summary.get("title") or {}).get("title") or {}).get("value")
        date = summary.get("publication-date") or {}
        year = (date.get("year") or {}).get("value")
        start = "-".join(part for part in (year, (date.get("month") or {}).get("value"), (date.get("day") or {}).get("value")) if part)
        venue = (summary.get("journal-title") or {}).get("value")
        ext_ids = (summary.get("external-ids") or {}).get("external-id", [])
        doi = next((e["external-id-value"] for e in ext_ids if e.get("external-id-type") == "doi"), None)
        rows.append(
            {
                "name": venue,
                "year": year,
                "start": start or None,
                "type": "workshop" if venue and "workshop" in venue.lower() else "conference",
                "role": "Poster" if summary.get("type") == "conference-poster" else "Talk",
                "summary": _quoted(title or ""),
                "doi": doi,
                "_title": title,
            }
        )
    return rows


def zenodo_events() -> list[dict]:
    """Slides and posters deposited on Zenodo under this ORCID, with the record's meeting details."""
    rows = []
    for page in range(1, 11):
        query = urllib.parse.urlencode({"q": f"creators.orcid:{ORCID_ID}", "size": 25, "page": page})
        hits = fetch_json(f"https://zenodo.org/api/records?{query}")["hits"]["hits"]
        for record in hits:
            meta = record["metadata"]
            meeting = meta.get("meeting") or {}
            upload_type = (meta.get("resource_type") or {}).get("type")
            if upload_type not in EVENT_RECORD_ROLES and not meeting:
                continue
            name = meeting.get("acronym") or meeting.get("title")
            rows.append(
                {
                    "name": name,
                    "year": (meta.get("publication_date") or "")[:4] or None,
                    "start": meta.get("publication_date"),
                    "location": meeting.get("place"),
                    "type": "workshop" if name and "workshop" in name.lower() else "conference",
                    "url": meeting.get("url"),
                    "role": EVENT_RECORD_ROLES.get(upload_type or ""),
                    "summary": _quoted(meta["title"]),
                    "doi": record.get("doi"),
                    "_concept": record.get("conceptdoi"),
                    "_title": meta["title"],
                }
            )
        if len(hits) < 25:
            break
    return rows


def update_events(works: dict) -> None:
    """Talks and posters from ORCID and Zenodo not already curated in events.yml.

    Records without a named venue are listed for review instead of being published half-empty.
    """
    curated = (DATA_DIR / "events.yml").read_text(encoding="utf-8")
    known = {d.lower() for d in re.findall(r"^\s*doi:\s*\"?([^\s\"]+)", curated, re.MULTILINE)}
    rows, review = [], []
    for row in orcid_events(works) + zenodo_events():
        dois = {str(row.get(k)).lower() for k in ("doi", "_concept") if row.get(k)}
        if dois & known:
            continue
        known |= dois
        if not row["name"]:
            review.append(f"{row['_title']} ({row.get('doi') or 'no DOI'})")
            continue
        rows.append({k: v for k, v in row.items() if not k.startswith("_")})
    rows.sort(key=lambda r: r.get("start") or r.get("year") or "0", reverse=True)
    path = DATA_DIR / "events_generated.yml"
    if rows:
        write_yaml_list(path, rows)
    else:
        path.write_text("[]\n", encoding="utf-8")
    print(f"events_generated.yml: {len(rows)} talks and posters from ORCID and Zenodo")
    for item in review:
        print(f"  needs a venue (add it to events.yml, or to the Zenodo/ORCID record): {item}")


def fetch_optional(url: str) -> dict | None:
    """Usage figures are best effort: a missing record or a failing service leaves them out."""
    try:
        return fetch_json(url)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"  skipped {url}: {error}")
        return None


def live(url: str | None) -> str | None:
    """The URL if it resolves, so dead links in package metadata are not published."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]}), timeout=30):
            return url
    except (urllib.error.URLError, TimeoutError, ValueError):
        print(f"  dropped a link that does not resolve: {url}")
        return None


def update_software(cv: dict) -> None:
    rows = []
    for item in cv.get("libraries", []):
        pkg = item.get("package")
        if not pkg:
            continue
        info = fetch_json(f"https://pypi.org/pypi/{pkg}/json")["info"]
        project_urls = info.get("project_urls") or {}
        rows.append(
            {
                "name": info["name"],
                "summary": info.get("summary") or "",
                "version": info.get("version"),
                "pypi_url": f"https://pypi.org/project/{pkg}/",
                "docs": live(project_urls.get("Documentation")),
            }
        )
    write_yaml_list(DATA_DIR / "software.yml", rows)
    print(f"software.yml: {len(rows)} packages from PyPI")


def _github_repo(url: str | None) -> str | None:
    match = re.match(r"https://github\.com/([^/]+/[^/#?]+)", url or "")
    return match.group(1).removesuffix(".git") if match else None


def github(path: str) -> Any:
    """The GitHub API, authenticated when GITHUB_TOKEN (or GH_TOKEN) is set: unauthenticated
    calls are limited to 60 an hour."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {**HEADERS, "Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com{path}", headers=headers), timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


WIKIDATA_API = "https://www.wikidata.org/w/api.php?"
# People lookups (Wikidata items and facts, GitHub profiles) are slow and rate-limited, so they
# are cached with the date checked and redone only when older than CACHE_DAYS (or with
# `people --refresh`). The cache is committed with the data.
CACHE_FILE = Path(__file__).resolve().parent / "cache" / "people.json"
CACHE_DAYS = 30
# Automation accounts that commit to repositories but are not marked as bots on GitHub.
AUTOMATION_ACCOUNTS = {"actions-user", "github-actions", "web-flow", "ghost"}


class Cache:
    def __init__(self, refresh: bool = False):
        self.entries: dict[str, dict] = {} if refresh or not CACHE_FILE.exists() else json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        self.today = time.strftime("%Y-%m-%d")

    def fresh(self, key: str) -> bool:
        entry = self.entries.get(key)
        return bool(entry) and time.mktime(time.strptime(entry["checked"], "%Y-%m-%d")) > time.time() - CACHE_DAYS * 86400

    def put(self, key: str, value: Any) -> None:
        self.entries[key] = {"checked": self.today, "value": value}

    def get(self, key: str, compute: Any) -> Any:
        """The cached value for key, computed again when missing or stale."""
        if not self.fresh(key):
            self.put(key, compute())
        return self.entries[key]["value"]

    def save(self) -> None:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(self.entries, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def wikidata_api(params: dict[str, str]) -> dict:
    """The Wikidata API at a gentle pace, waiting and retrying when asked to slow down (429)."""
    for attempt in range(5):
        time.sleep(0.4)
        try:
            return fetch_json(WIKIDATA_API + urllib.parse.urlencode({**params, "format": "json"}))
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(int(error.headers.get("Retry-After") or 5 * (attempt + 1)))
    raise TimeoutError("Wikidata kept asking to slow down")


def best_rank(statements: list[dict]) -> list[dict]:
    """Wikidata's best-ranked statements: the preferred ones if any, else the normal ones; never
    deprecated ones (values kept only as a record of being wrong or obsolete)."""
    preferred = [s for s in statements if s.get("rank") == "preferred"]
    return preferred or [s for s in statements if s.get("rank", "normal") == "normal"]


def wikidata_item(prop: str, value: str) -> str | None:
    """The one Wikidata item with this identifier (e.g. P496, an ORCID), if exactly one has it."""
    try:
        hits = wikidata_api({"action": "query", "list": "search", "srsearch": f"haswbstatement:{prop}={value}"})["query"]["search"]
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        return None
    return hits[0]["title"] if len(hits) == 1 else None


def wikidata_facts(qids: list[str]) -> list[dict[str, str]]:
    """For each item: its IRI, English label, ORCID (P496), GitHub username (P2037), and official
    websites (P856), one row per website (or one row without), as rows of plain values."""
    rows = []
    for start in range(0, len(qids), 50):
        params = {"action": "wbgetentities", "ids": "|".join(qids[start : start + 50]), "props": "claims|labels", "languages": "en"}
        for qid, entity in wikidata_api(params).get("entities", {}).items():
            claims = entity.get("claims", {})

            def values(prop: str, claims: dict = claims) -> list[str]:
                return [c["mainsnak"]["datavalue"]["value"] for c in best_rank(claims.get(prop, [])) if "datavalue" in c["mainsnak"]]

            base: dict = {"item": f"http://www.wikidata.org/entity/{qid}"}
            base["ids"] = [
                [prop, c["mainsnak"]["datavalue"]["value"]]
                for prop, claims_of in claims.items() for c in best_rank(claims_of)
                if c["mainsnak"].get("datatype") == "external-id" and "datavalue" in c["mainsnak"]
            ]
            label = entity.get("labels", {}).get("en", {}).get("value")
            for key, found in (("label", [label] if label else []), ("orcid", values("P496")), ("gh", values("P2037"))):
                if found:
                    base[key] = found[0]
            rows += [{**base, "site": site} for site in values("P856")] or [base]
    return rows


def wikidata_formatters(props: list[str]) -> dict[str, dict[str, str]]:
    """For identifier properties: their English label and the URL pattern (P1630) that turns a
    value into a link, where they have one."""
    found = {}
    for start in range(0, len(props), 50):
        params = {"action": "wbgetentities", "ids": "|".join(props[start : start + 50]), "props": "claims|labels", "languages": "en"}
        for prop, entity in wikidata_api(params).get("entities", {}).items():
            pattern = [c["mainsnak"]["datavalue"]["value"] for c in best_rank(entity.get("claims", {}).get("P1630", [])) if "datavalue" in c["mainsnak"]]
            label = entity.get("labels", {}).get("en", {}).get("value")
            if pattern and label:
                found[prop] = {"label": label, "pattern": pattern[0]}
    return found


CITATION_FILES = ("CITATION.cff", ".zenodo.json", "codemeta.json")


def github_raw(repo: str, path: str) -> str | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/HEAD/{path}", headers=HEADERS), timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def citation_authors(repo: str, path: str) -> list[dict[str, str]]:
    """The authors a citation file (CITATION.cff, .zenodo.json, codemeta.json) lists with an
    ORCID, as full names."""
    text = github_raw(repo, path)
    if not text:
        return []
    try:
        data = yaml.safe_load(text) if path.endswith(".cff") else json.loads(text)
    except (yaml.YAMLError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    people = data.get("authors") or data.get("creators") or data.get("author") or []
    people = people if isinstance(people, list) else [people]
    found = []
    for p in people:
        if not isinstance(p, dict):
            continue
        orcid = str(p.get("orcid") or p.get("@id") or p.get("identifier") or "")
        match = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", orcid)
        name = p.get("name") or " ".join(v for v in (p.get("given-names") or p.get("givenName"), p.get("family-names") or p.get("familyName")) if v)
        if "," in name:  # "Family, Given"
            family, _, given = name.partition(",")
            name = f"{given.strip()} {family.strip()}"
        if match and name.strip():
            found.append({"name": name.strip(), "orcid": f"https://orcid.org/{match.group(0)}", "file": f"https://github.com/{repo}/blob/HEAD/{path}"})
    return found


def own_citation_files(login: str) -> list[tuple[str, str]]:
    """Citation files in repositories the account owns that mention an ORCID (GitHub code search,
    paced to its rate limit)."""
    time.sleep(7)
    try:
        found = github(f"/search/code?q={urllib.parse.quote(f'orcid user:{login}')}&per_page=30")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    return sorted({(i["repository"]["full_name"], i["path"]) for i in found.get("items", []) if i["path"].rsplit("/", 1)[-1] in CITATION_FILES})


def openalex_ids(orcid: str) -> dict[str, str]:
    """The identifiers OpenAlex links to an author, found by ORCID: links only."""
    try:
        ids = fetch_json(f"https://api.openalex.org/authors/orcid:{orcid.rsplit('/', 1)[-1]}").get("ids") or {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {}
    return {key: value for key, value in ids.items() if isinstance(value, str) and value.startswith("http")}


def orcid_identifiers(orcid: str) -> list[dict[str, str]]:
    """The external identifiers a person added to their ORCID record, as labeled links."""
    try:
        record = fetch_json(f"https://pub.orcid.org/v3.0/{orcid.rsplit('/', 1)[-1]}/external-identifiers")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    return [
        {"label": i["external-id-type"], "url": i["external-id-url"]["value"]}
        for i in record.get("external-identifier", []) if (i.get("external-id-url") or {}).get("value")
    ]


def own_links(own: dict, me: str) -> list[dict[str, str]]:
    """Every link the sources give for the CV's subject that the page header does not already
    show, labeled (by the source's name for it, else by host, as in _data/concepts.yml), with its
    kind: a profile (the person elsewhere) or a website (a site of theirs)."""
    site = yaml.safe_load((DATA_DIR.parent / "_config.yml").read_text(encoding="utf-8"))["canonical"]
    hosts = yaml.safe_load((DATA_DIR / "concepts.yml").read_text(encoding="utf-8"))["hosts"]
    shown = {me, site, *(p["url"] for p in PERSON["profiles"] if p.get("header"))}
    def key(url: str) -> str:
        """One key for the several addresses of one record (an entity IRI, its wiki page)."""
        parsed = urllib.parse.urlparse(url.lower().rstrip("/"))
        return parsed.netloc.removeprefix("www.") + "/" + parsed.path.rsplit("/", 1)[-1]

    shown = {key(u) for u in shown}
    candidates = [
        *({"url": u, "source": "", "kind": "profile"} for u in own["same_as"]),
        *({"url": p["url"], "source": "", "kind": "profile"} for p in PERSON["profiles"] if not p.get("header")),
        *({"url": a["url"], "source": a.get("source") or me, "kind": "profile"} for a in own["accounts"]),
        *({**p, "kind": "profile"} for p in own["profiles"]),
        *({**w, "kind": "website"} for w in own["websites"]),
    ]
    links, seen = [], set(shown)
    for c in candidates:
        if key(c["url"]) in seen:
            continue
        seen.add(key(c["url"]))
        host = urllib.parse.urlparse(c["url"]).netloc
        label = c.get("label") or hosts.get(host) or host.removeprefix("www.")
        source_host = urllib.parse.urlparse(c["source"]).netloc
        via = hosts.get(source_host) or source_host.removeprefix("www.")
        links.append({k: v for k, v in {"url": c["url"], "label": label, "kind": c["kind"], "via": via if via != label else ""}.items() if v})
    return sorted(links, key=lambda link: link["label"].casefold())


def update_people(cv: dict, refresh: bool = False) -> None:
    """Everyone who contributed to the listed software (GitHub) or co-authored a publication
    (ORCID), with what Wikidata knows of them: their item, ORCID, GitHub account, and official
    website. Each name and website keeps its source; nothing is harmonized. People are
    identified by ORCID when one is known, else by their GitHub profile.

    A GitHub account is tied to an ORCID by the strongest evidence available, recorded with it:
    1. declared by the person, on their ORCID record (a github.com link) or GitHub profile (an
       orcid.org link);
    2. Wikidata, an item with both the GitHub username (P2037) and the ORCID (P496);
    3. declared in citation metadata (CITATION.cff, .zenodo.json, codemeta.json) of the
       account's own repositories or of the software it contributed to, for the author whose
       name fits the account;
    4. inferred, only when exactly one co-author fits: the login or the profile's name is the
       co-author's name and they co-authored a paper describing software the account
       contributed to (the paper is the evidence), or, weaker, the profile's name is exactly
       the co-author's name."""
    me = f"https://orcid.org/{ORCID_ID}"
    cache = Cache(refresh)
    contributors: dict[str, list[str]] = {}
    for item in [*cv.get("libraries", []), *cv.get("contributions", []), *cv.get("personal_tools", [])]:
        repo = _github_repo(item.get("repository"))
        if not repo:
            continue
        try:
            listed = github(f"/repos/{repo}/contributors?per_page=100")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            print(f"  skipped contributors of {repo}: {error}")
            continue
        for account in listed if isinstance(listed, list) else []:
            login = account.get("login", "")
            if account.get("type") == "Bot" or login.endswith("[bot]") or login.lower() in AUTOMATION_ACCOUNTS:
                continue
            contributors.setdefault(login, []).append(item["id"])

    authors = sorted({
        a["orcid"] for row in yaml.safe_load((DATA_DIR / "publications.yml").read_text(encoding="utf-8"))
        for a in row.get("authors") or [] if a.get("orcid") and a["orcid"] != me
    })
    logins = sorted(login for login in contributors if login.lower() != GITHUB_USER.lower())
    def facts(qids: set[str]) -> list[dict[str, str]]:
        """Wikidata facts for these items: stale ones fetched together, then cached one by one."""
        stale = sorted(q for q in qids if not cache.fresh(f"wikidata:{q}"))
        fetched: dict[str, list[dict[str, str]]] = {q: [] for q in stale}
        for r in wikidata_facts(stale) if stale else []:
            fetched[r["item"].rsplit("/", 1)[-1]].append(r)
        for q, rows in fetched.items():
            cache.put(f"wikidata:{q}", rows)
        return [r for q in sorted(qids) for r in cache.entries[f"wikidata:{q}"]["value"]]

    github_items = {login: cache.get(f"github-item:{login.lower()}", lambda login=login: wikidata_item("P2037", login)) for login in logins}
    by_github: dict[str, list[dict[str, str]]] = {}
    for r in facts({q for q in github_items.values() if q}):
        for login, qid in github_items.items():
            if qid and r["item"].endswith(f"/{qid}"):
                by_github.setdefault(login.lower(), []).append(r)
    orcids = sorted({ORCID_ID, *(o.rsplit("/", 1)[-1] for o in authors), *(r["orcid"] for rs in by_github.values() for r in rs if r.get("orcid"))})
    orcid_items = {o: cache.get(f"orcid-item:{o}", lambda o=o: wikidata_item("P496", o)) for o in orcids}
    by_orcid: dict[str, list[dict[str, str]]] = {}
    for r in facts({q for q in orcid_items.values() if q}):
        for orcid, qid in orcid_items.items():
            if qid and r["item"].endswith(f"/{qid}"):
                by_orcid.setdefault(orcid, []).append(r)

    publications = yaml.safe_load((DATA_DIR / "publications.yml").read_text(encoding="utf-8"))
    names_by_orcid: dict[str, set[tuple[str, str]]] = {}
    papers_by_orcid: dict[str, set[str]] = {}
    for row in publications:
        for a in row.get("authors") or []:
            if a.get("orcid") and a["orcid"] != me:
                names_by_orcid.setdefault(a["orcid"], set()).add((a.get("given", ""), a["family"]))
                if row.get("doi"):
                    papers_by_orcid.setdefault(a["orcid"], set()).add(row["doi"])
    software_papers = {i["id"]: set(i.get("publications") or []) for i in [*cv.get("libraries", []), *cv.get("contributions", []), *cv.get("personal_tools", [])]}

    def profile(login: str) -> dict:
        try:
            user = github(f"/users/{login}")
        except (urllib.error.URLError, TimeoutError, ValueError):
            return {}
        return {"name": user.get("name"), "blog": user.get("blog")} if isinstance(user, dict) else {}

    def social(login: str) -> list[str]:
        try:
            accounts = github(f"/users/{login}/social_accounts")
        except (urllib.error.URLError, TimeoutError, ValueError):
            return []
        return [a.get("url", "") for a in accounts] if isinstance(accounts, list) else []

    def researcher_urls(orcid: str) -> list[str]:
        try:
            record = fetch_json(f"https://pub.orcid.org/v3.0/{orcid.rsplit('/', 1)[-1]}/researcher-urls")
        except (urllib.error.URLError, TimeoutError, ValueError):
            return []
        return [u["url"]["value"] for u in record.get("researcher-url", []) if u.get("url")]

    users = {login: cache.get(f"github-user:{login.lower()}", lambda login=login: profile(login)) for login in logins}
    by_login = {login.lower(): login for login in logins}
    identity: dict[str, tuple[str, str, str | None]] = {}  # login → (ORCID, how, evidence IRI)
    declared_accounts: dict[str, set[str]] = {}  # ORCID → GitHub profiles its record lists
    for orcid in names_by_orcid:
        for url in cache.get(f"orcid-urls:{orcid}", lambda orcid=orcid: researcher_urls(orcid)):
            match = re.match(r"https?://(?:www\.)?github\.com/([^/?#]+)/?$", url)
            if match:
                declared_accounts.setdefault(orcid, set()).add(match.group(1).lower())
                if match.group(1).lower() in by_login:
                    identity.setdefault(by_login[match.group(1).lower()], (orcid, "declared on ORCID record", orcid))
    tracked_citations = {
        item["id"]: [a for path in CITATION_FILES for a in cache.get(f"citation:{repo}/{path}", lambda repo=repo, path=path: citation_authors(repo, path))]
        for item in [*cv.get("libraries", []), *cv.get("contributions", []), *cv.get("personal_tools", [])]
        if (repo := _github_repo(item.get("repository")))
    }

    def fits_account(login: str, name: str) -> bool:
        shown = (users.get(login) or {}).get("name") or ""
        compact = re.sub(r"[^a-z0-9]", "", _fold(login))
        return (bool(shown) and _full_name(shown, "") == _full_name(name, "")) or re.sub(r"[^a-z0-9]", "", _fold(name)) == compact

    for login in logins:
        profile_url = f"https://github.com/{login}"
        readme = cache.get(f"github-readme:{login.lower()}", lambda login=login: github_raw(f"{login}/{login}", "README.md") or "")
        declared = [f"https://orcid.org/{m}" for m in re.findall(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", readme)]
        for url in cache.get(f"github-social:{login.lower()}", lambda login=login: social(login)):
            declared += [f"https://orcid.org/{m}" for m in re.findall(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", url)]
        if len(set(declared)) == 1:
            identity.setdefault(login, (declared[0], "declared on GitHub profile", profile_url))
        if login in identity:
            continue
        cited = [
            a for repo, path in cache.get(f"github-citations:{login.lower()}", lambda login=login: own_citation_files(login))
            for a in cache.get(f"citation:{repo}/{path}", lambda repo=repo, path=path: citation_authors(repo, path))
        ]
        cited += [a for software in contributors[login] for a in tracked_citations.get(software, [])]
        fitting = {a["orcid"]: a["file"] for a in cited if fits_account(login, a["name"])}
        if len(fitting) == 1:
            orcid, file = next(iter(fitting.items()))
            identity[login] = (orcid, "declared in citation metadata", file)
        found = by_github.get(login.lower(), [])
        wikidata_orcid = next((r for r in found if r.get("orcid")), None)
        if wikidata_orcid:
            identity.setdefault(login, (f"https://orcid.org/{wikidata_orcid['orcid']}", "Wikidata", wikidata_orcid["item"].replace("https://", "http://")))
        if login in identity:
            continue
        compact = re.sub(r"[^a-z0-9]", "", _fold(login))
        shown = (users.get(login) or {}).get("name") or ""
        fits = [
            orcid for orcid, names in names_by_orcid.items()
            if any(re.sub(r"[^a-z0-9]", "", _fold(g + f)) == compact or (shown and _full_name(shown, "") == _full_name(g, f)) for g, f in names)
        ]
        papers = [doi for software in contributors[login] for doi in software_papers.get(software, set()) if fits and doi in papers_by_orcid.get(fits[0], set())]
        if len(fits) == 1 and papers:
            identity[login] = (fits[0], "inferred from name and co-authorship", f"https://doi.org/{papers[0]}")
            continue
        same_name = [orcid for orcid, names in names_by_orcid.items() if shown and any(_full_name(shown, "") == _full_name(g, f) for g, f in names)]
        if len(same_name) == 1:
            identity[login] = (same_name[0], "inferred from matching name", f"https://github.com/{login}")

    identifier_props = sorted({prop for rs in [*by_github.values(), *by_orcid.values()] for r in rs for prop, _ in r.get("ids") or []})
    stale_props = [prop for prop in identifier_props if not cache.fresh(f"wikidata-property:{prop}")]
    fetched_props = wikidata_formatters(stale_props) if stale_props else {}
    for prop in stale_props:
        cache.put(f"wikidata-property:{prop}", fetched_props.get(prop))
    formatters = {prop: cache.entries[f"wikidata-property:{prop}"]["value"] for prop in identifier_props if cache.entries[f"wikidata-property:{prop}"]["value"]}

    people: dict[str, dict] = {}

    def person(key: str) -> dict:
        return people.setdefault(key, {"id": key, "names": [], "same_as": [], "accounts": [], "websites": [], "profiles": [], "contributes": []})

    def add_profile(entry: dict, url: str, label: str | None, source: str) -> None:
        known = {u.lower().rstrip("/") for u in [entry["id"], *entry["same_as"], *(p["url"] for p in entry["profiles"]), *(a["url"] for a in entry["accounts"])]}
        if url.lower().rstrip("/") not in known:
            entry["profiles"].append({k: v for k, v in {"url": url, "label": label, "source": source}.items() if v})

    def add_wikidata(entry: dict, rows: list[dict]) -> None:
        for r in rows:
            item = r["item"].replace("https://", "http://")
            if item not in entry["same_as"]:
                entry["same_as"].append(item)
            if r.get("label") and {"name": r["label"], "source": item} not in entry["names"]:
                entry["names"].append({"name": r["label"], "source": item})
            if r.get("site") and {"url": r["site"], "source": item} not in entry["websites"]:
                entry["websites"].append({"url": r["site"], "source": item})
            account = f"https://github.com/{r['gh']}" if r.get("gh") else None
            if account and account.lower() not in {u.lower() for u in entry["same_as"]}:
                entry["same_as"].append(account)
            if account and account.lower() not in {a["url"].lower() for a in entry["accounts"]}:
                entry["accounts"].append({"url": account, "via": "Wikidata", "source": item})
            for prop, value in r.get("ids") or []:
                if prop in ("P496", "P2037") or prop not in formatters:
                    continue
                add_profile(entry, formatters[prop]["pattern"].replace("$1", urllib.parse.quote(str(value), safe="")), formatters[prop]["label"], item)

    for login in logins:
        profile_url = f"https://github.com/{login}"
        found = by_github.get(login.lower(), [])
        orcid, how, evidence = identity.get(login, (None, None, None))
        entry = person(orcid or profile_url)
        entry["contributes"] += [i for i in contributors[login] if i not in entry["contributes"]]
        if orcid:
            if profile_url not in entry["same_as"]:
                entry["same_as"].append(profile_url)
            entry["accounts"].append({"url": profile_url, "via": how, "source": evidence})
        user = users.get(login)
        if isinstance(user, dict):
            entry["names"].append({"name": user.get("name") or login, "source": profile_url})
            blog = (user.get("blog") or "").strip()
            if blog:
                blog = blog if blog.startswith("http") else f"https://{blog}"
                entry["websites"].append({"url": blog, "source": profile_url})
        add_wikidata(entry, found)
    for orcid in [a.rsplit("/", 1)[-1] for a in authors] + [o for o in orcids if f"https://orcid.org/{o}" in people]:
        if by_orcid.get(orcid):
            add_wikidata(person(f"https://orcid.org/{orcid}"), by_orcid[orcid])
    # What co-authors' ORCID records say of them: their web links and external identifiers.
    for orcid in names_by_orcid:
        if orcid == me:
            continue
        entry = person(orcid)
        for url in cache.get(f"orcid-urls:{orcid}", lambda orcid=orcid: researcher_urls(orcid)):
            if "github.com" not in url and {"url": url, "source": orcid} not in entry["websites"]:
                entry["websites"].append({"url": url, "source": orcid})
        for identifier in cache.get(f"orcid-ids:{orcid}", lambda orcid=orcid: orcid_identifiers(orcid)):
            add_profile(entry, identifier["url"], identifier["label"], orcid)
    # What OpenAlex links to each identified person, and the links on contributors' GitHub profiles.
    for orcid in [*names_by_orcid, me]:
        ids = cache.get(f"openalex:{orcid}", lambda orcid=orcid: openalex_ids(orcid))
        for key, url in sorted(ids.items()):
            if key != "orcid":
                add_profile(person(orcid), url, None, ids.get("openalex", url))
    for login in [*logins, GITHUB_USER]:
        orcid = me if login == GITHUB_USER else identity.get(login, (None, None, None))[0]
        profile_url = f"https://github.com/{login}"
        for url in cache.get(f"github-social:{login.lower()}", lambda login=login: social(login)):
            if "orcid.org" not in url:
                add_profile(person(orcid or profile_url), url, None, profile_url)
    # Accounts a co-author's ORCID record lists, even when they did not contribute here.
    for orcid, listed in declared_accounts.items():
        entry = person(orcid)
        for login in sorted(listed):
            url = f"https://github.com/{by_login.get(login, login)}"
            if url.lower() not in {u.lower() for u in entry["same_as"]}:
                entry["same_as"].append(url)
            if url.lower() not in {a["url"].lower() for a in entry["accounts"]}:
                entry["accounts"].append({"url": url, "via": "declared on ORCID record", "source": orcid})
    # The CV's subject: what their own ORCID record and Wikidata item link to.
    own = person(me)
    for url in cache.get(f"orcid-urls:{me}", lambda: researcher_urls(me)):
        if {"url": url, "source": me} not in own["websites"]:
            own["websites"].append({"url": url, "source": me})
    for identifier in cache.get(f"orcid-ids:{me}", lambda: orcid_identifiers(me)):
        add_profile(own, identifier["url"], identifier["label"], me)
    for r in by_orcid.get(ORCID_ID, []):
        item = r["item"].replace("https://", "http://")
        if item not in own["same_as"]:
            own["same_as"].append(item)
        if r.get("site") and {"url": r["site"], "source": item} not in own["websites"]:
            own["websites"].append({"url": r["site"], "source": item})
        for prop, value in r.get("ids") or []:
            if prop != "P496" and prop in formatters:
                add_profile(own, formatters[prop]["pattern"].replace("$1", urllib.parse.quote(str(value), safe="")), formatters[prop]["label"], item)
    github_account = f"https://github.com/{GITHUB_USER}"
    if github_account.lower() not in {a["url"].lower() for a in own["accounts"]}:
        own["accounts"].append({"url": github_account, "via": "cv.yml", "source": me})
    own["links"] = own_links(own, me)
    mine = [i for login, ids in contributors.items() if login.lower() == GITHUB_USER.lower() for i in ids]
    if mine:
        person(me)["contributes"] = mine
    cache.save()
    rows = [{k: v for k, v in p.items() if v} for p in sorted(people.values(), key=lambda p: p["id"])]
    (DATA_DIR / "people.yml").write_text(
        "# Written by scripts/update_cv_data.py: contributors (GitHub) and co-authors (ORCID), with what Wikidata\n"
        "# knows of them. Every name and website keeps its source.\n"
        + yaml.safe_dump(rows, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    print(f"people.yml: {len(rows)} people ({len(logins)} contributors, {len(authors)} co-authors with ORCID)")


def update_code_stats(cv: dict) -> None:
    """Downloads, Docker pulls, stars, forks, and dependents for every listed package or repository."""
    rows = []
    entries = [*cv.get("libraries", []), *cv.get("contributions", []), *cv.get("personal_tools", [])]
    for item in entries:
        row: dict = {"id": item["id"]}
        if item.get("package"):
            data = fetch_optional(f"https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/{item['package']}")
            if data:
                row["downloads"] = data.get("downloads")
                row["downloads_period"] = data.get("downloads_period")
                row["dependent_repos"] = data.get("dependent_repos_count")
                row["dependent_packages"] = data.get("dependent_packages_count")
                row["downloads_source"] = f"https://packages.ecosyste.ms/registries/pypi.org/packages/{item['package']}"
        if item.get("docker"):
            data = fetch_optional(f"https://hub.docker.com/v2/repositories/{item['docker']}/")
            if data:
                row["docker_pulls"] = data.get("pull_count")
                row["docker_source"] = f"https://hub.docker.com/r/{item['docker']}"
        repo = _github_repo(item.get("repository"))
        if repo:
            data = fetch_optional(f"https://repos.ecosyste.ms/api/v1/hosts/GitHub/repositories/{repo}")
            if not data or "stargazers_count" not in data:
                data = fetch_optional(f"https://api.github.com/repos/{repo}")
            if data:
                row["stars"] = data.get("stargazers_count")
                row["forks"] = data.get("forks_count")
                row["repository_source"] = f"https://github.com/{repo}"
        rows.append({k: v for k, v in row.items() if v is not None})
    write_yaml_list(DATA_DIR / "code_stats.yml", rows)
    print(f"code_stats.yml: usage figures for {len(rows)} packages and repositories")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if sys.argv[1:2] == ["people"]:  # only contributors and co-authors; --refresh ignores the cache
        update_people(yaml.safe_load((DATA_DIR / "cv.yml").read_text(encoding="utf-8")), refresh="--refresh" in sys.argv)
        return
    if sys.argv[1:2] == ["authors"]:  # only reconcile the authors in publications.yml; --refresh ignores the cache
        rows = yaml.safe_load((DATA_DIR / "publications.yml").read_text(encoding="utf-8"))
        reconcile_authors(rows, refresh="--refresh" in sys.argv)
        write_yaml_list(DATA_DIR / "publications.yml", rows)
        return
    cv = yaml.safe_load((DATA_DIR / "cv.yml").read_text(encoding="utf-8"))
    works = fetch_json(f"https://pub.orcid.org/v3.0/{ORCID_ID}/works")
    update_publications(works)
    update_events(works)
    update_software(cv)
    update_code_stats(cv)
    update_people(cv)


if __name__ == "__main__":
    main()
