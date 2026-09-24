#!/usr/bin/env python3
"""Update publication, event, software, and usage data from ORCID, Crossref, Zenodo, PyPI, ecosyste.ms, GitHub, and Docker Hub."""
from __future__ import annotations

import json
import re
import socket
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

# Prefer IPv4 when both address families are available.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_preferred_getaddrinfo(*args, **kwargs):
    results = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 or results


socket.getaddrinfo = _ipv4_preferred_getaddrinfo

ORCID_ID = "0000-0002-4166-7093"
GITHUB_USER = "jmillanacosta"
# Additional publications.
EXTRA_WORKS = [
    {
        "title": "LP-63 Making the AOP-Wiki knowledge graph usable across disciplines in toxicological assessment",
        "year": "2025",
        "journal": "Toxicology Letters",
        "type": "conference-abstract",
        "doi": "10.1016/j.toxlet.2025.07.1074",
    },
    {
        "title": "Extended RDF support for Biomedical Knowledge Graphs in pyBioDataFuse: on-the-fly RDF graph generation and new resource annotators",
        "year": "2025",
        "journal": "CEUR Workshop Proceedings (SWAT4HCLS 2025)",
        "type": "conference-paper",
        "doi": "10.24406/publica-8969",
    },
    {
        "title": "MCP server tools with RDF shapes",
        "year": "2025",
        "journal": None,
        "type": "preprint",
        "doi": "10.37044/osf.io/8qeh5_v1",
    },
]


# ORCID work types that are talks or posters; they feed events, not publications.
EVENT_WORK_TYPES = {"lecture-speech", "conference-poster"}
# Zenodo upload types that are talks or posters.
EVENT_RECORD_ROLES = {"presentation": "Talk", "poster": "Poster"}

DATA_DIR = Path(__file__).resolve().parent.parent / "_data"
HEADERS = {"User-Agent": f"{GITHUB_USER}-cv-updater", "Accept": "application/json"}


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
    if orcid:
        return orcid.rstrip("/").endswith(ORCID_ID)
    full = _fold(f"{given} {family}")
    return "acosta" in full and ("millan" in full or "javier" in full)


def authors(doi: str) -> list[dict]:
    """Ordered author list from Crossref, or DataCite for DOIs Crossref does not register.

    Each author has given and family names, an ORCID IRI when the metadata has one, and `me` for this CV's
    subject (matched by ORCID, else by name, since registries split "Millán Acosta" inconsistently).
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
        for extra in EXTRA_WORKS
        if extra["doi"] not in known_dois
    )

    rows.sort(key=lambda r: r.get("year") or "0", reverse=True)
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
    cv = yaml.safe_load((DATA_DIR / "cv.yml").read_text(encoding="utf-8"))
    works = fetch_json(f"https://pub.orcid.org/v3.0/{ORCID_ID}/works")
    update_publications(works)
    update_events(works)
    update_software(cv)
    update_code_stats(cv)


if __name__ == "__main__":
    main()
