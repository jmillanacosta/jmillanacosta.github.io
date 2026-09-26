#!/usr/bin/env python3
"""Refresh the generated data in _data from public sources.

    python scripts/update_cv_data.py            everything: publications, events, software, usage, repositories, collaborators
    python scripts/update_cv_data.py authors    only identify the authors in _data/publications.yml
    python scripts/update_cv_data.py collaborators  only contributors and co-authors (_data/collaborators.yml)
    python scripts/update_cv_data.py repositories   only repository owners and skills (_data/repositories.yml)

Add --refresh to `authors` or `collaborators` to redo the cached lookups. Set GITHUB_TOKEN (or GH_TOKEN)
to lift GitHub's limit of 60 calls an hour. Settings are in _config.yml, under `updates`.
"""

import sys

import yaml
from cvdata.collaborators import update_collaborators
from cvdata.config import DATA_DIR, ORCID_ID
from cvdata.events import update_events
from cvdata.files import write_yaml_list
from cvdata.net import fetch_json
from cvdata.publications import reconcile_authors, update_publications
from cvdata.software import update_code_stats, update_repositories, update_software


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    refresh = "--refresh" in sys.argv
    cv = yaml.safe_load((DATA_DIR / "cv.yml").read_text(encoding="utf-8"))
    if sys.argv[1:2] == ["collaborators"]:
        update_collaborators(cv, refresh=refresh)
        return
    if sys.argv[1:2] == ["repositories"]:
        update_repositories(cv)
        return
    if sys.argv[1:2] == ["authors"]:
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
