"""Talks and posters from ORCID and Zenodo."""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

from .config import DATA_DIR, EVENT_WORK_TYPES, ORCID_ID
from .files import write_yaml_list
from .net import fetch_json

# Zenodo upload types that are talks or posters.
EVENT_RECORD_ROLES = {"presentation": "Talk", "poster": "Poster"}


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
