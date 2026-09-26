#!/usr/bin/env python3
"""Public data is refreshed in _data. Options are listed with --help."""

import argparse

import yaml
from cvdata.collaborators import update_collaborators
from cvdata.config import DATA_DIR, ORCID_ID
from cvdata.events import update_event_details, update_events
from cvdata.files import write_yaml_list
from cvdata.net import fetch_json
from cvdata.publications import add_subjects, add_topics, reconcile_authors, update_publications
from cvdata.software import update_code_stats, update_repositories, update_software


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part", nargs="?", default="all", choices=["all", "authors", "collaborators", "repositories", "wikidata", "topics", "organizations"])
    parser.add_argument("--refresh", action="store_true", help="cached lookups are repeated")
    args = parser.parse_args()
    refresh = args.refresh
    cv = yaml.safe_load((DATA_DIR / "cv.yml").read_text(encoding="utf-8"))
    if args.part == "wikidata":
        rows = yaml.safe_load((DATA_DIR / "publications.yml").read_text(encoding="utf-8")) or []
        add_subjects(rows)
        update_event_details()
        write_yaml_list(DATA_DIR / "publications.yml", rows)
        return
    if args.part == "topics":
        rows = yaml.safe_load((DATA_DIR / "publications.yml").read_text(encoding="utf-8")) or []
        add_topics(rows)
        write_yaml_list(DATA_DIR / "publications.yml", rows)
        return
    if args.part == "collaborators":
        update_collaborators(cv, refresh=refresh)
        return
    if args.part == "repositories":
        update_repositories(cv, refresh=refresh)
        return
    if args.part == "authors":
        rows = yaml.safe_load((DATA_DIR / "publications.yml").read_text(encoding="utf-8"))
        reconcile_authors(rows, refresh=refresh)
        write_yaml_list(DATA_DIR / "publications.yml", rows)
        return
    works = fetch_json(f"https://pub.orcid.org/v3.0/{ORCID_ID}/works")
    update_publications(works)
    update_events(works)
    update_software(cv)
    update_code_stats(cv)
    update_repositories(cv)
    update_collaborators(cv)


if __name__ == "__main__":
    main()
