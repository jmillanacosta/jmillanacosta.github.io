#!/usr/bin/env python3
"""Update publication and software metadata from ORCID, Crossref, GitHub and PyPI."""
from __future__ import annotations

import json
import socket
import unicodedata
import urllib.request
from pathlib import Path

# Prefer IPv4 when both address families are available.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_preferred_getaddrinfo(*args, **kwargs):
    results = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 or results


socket.getaddrinfo = _ipv4_preferred_getaddrinfo

ORCID_ID = "0000-0002-4166-7093"
GITHUB_USER = "jmillanacosta"
# Maintained personal packages.
PYPI_PACKAGES = ["pysec2pri", "mapkgsutils", "rdfsolve"]

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
        "title": "MCP server tools with RDF shapes",
        "year": "2025",
        "journal": None,
        "type": "preprint",
        "doi": "10.37044/osf.io/8qeh5_v1",
    },
]

# Fallback for packages whose PyPI project_urls metadata is wrong/missing
# (e.g. mapkgsutils publishes "https://github.com//mapkgsutils").
REPO_URL_OVERRIDES = {
    "mapkgsutils": "https://github.com/jmillanacosta/mapkgsutils",
    "pysec2pri": "https://github.com/jmillanacosta/pysec2pri",
}

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
            lines.append(f"{prefix}{key}: {yaml_str(str(value))}")
            first = False
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fold(s: str) -> str:
    """Strip accents and lowercase, for tolerant name matching."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()


def author_position(doi: str) -> str | None:
    """'N of M' position of Javier Millán Acosta among a DOI's authors, via Crossref."""
    try:
        data = fetch_json(f"https://api.crossref.org/works/{doi}")
    except Exception:
        return None
    authors = data.get("message", {}).get("author") or []
    if not authors:
        return None
    for i, a in enumerate(authors):
        family = _fold(a.get("family") or "")
        if "millan" in family or "acosta" in family:
            return f"{i + 1} of {len(authors)}"
    return None


def update_publications() -> None:
    works = fetch_json(f"https://pub.orcid.org/v3.0/{ORCID_ID}/works")
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
                "position": author_position(doi) if doi else None,
            }
        )
    known_dois = {r["doi"] for r in rows if r.get("doi")}
    for extra in EXTRA_WORKS:
        if extra["doi"] not in known_dois:
            rows.append(
                {
                    **extra,
                    "url": f"https://doi.org/{extra['doi']}",
                    "position": author_position(extra["doi"]),
                }
            )

    rows.sort(key=lambda r: r.get("year") or "0", reverse=True)
    write_yaml_list(DATA_DIR / "publications.yml", rows)
    print(f"publications.yml: {len(rows)} works ({len(rows) - len(EXTRA_WORKS)} from ORCID, {len(EXTRA_WORKS)} manually tracked)")


def update_software() -> None:
    rows = []
    for pkg in PYPI_PACKAGES:
        info = fetch_json(f"https://pypi.org/pypi/{pkg}/json")["info"]
        project_urls = info.get("project_urls") or {}
        repo_url = REPO_URL_OVERRIDES.get(pkg) or project_urls.get("Repository") or project_urls.get("Homepage")
        rows.append(
            {
                "name": info["name"],
                "summary": info.get("summary") or "",
                "version": info.get("version"),
                "pypi_url": f"https://pypi.org/project/{pkg}/",
                "repository": repo_url,
                "docs": project_urls.get("Documentation"),
            }
        )
    write_yaml_list(DATA_DIR / "software.yml", rows)
    print(f"software.yml: {len(rows)} packages from PyPI")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    update_publications()
    update_software()


if __name__ == "__main__":
    main()
