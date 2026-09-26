"""Settings are read from _config.yml and _data/cv.yml."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "_data"
CONFIG = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))
UPDATES = CONFIG["updates"]
PERSON = yaml.safe_load((DATA_DIR / "cv.yml").read_text(encoding="utf-8"))["person"]

ORCID_ID = str(PERSON["orcid"])
GITHUB_USER = next(p["url"] for p in PERSON["profiles"] if p["label"] == "GitHub").rstrip("/").rsplit("/", 1)[-1]
SITE_URL = CONFIG["canonical"]
HEADERS = {"User-Agent": f"cv-updater/1.0 ({SITE_URL})", "Accept": "application/json"}

CACHE_FILE = ROOT / UPDATES["cache_file"]
CACHE_DAYS = int(UPDATES["cache_days"])
IGNORED_ACCOUNTS = {a.lower() for a in UPDATES["ignored_accounts"]}
CITATION_FILES = tuple(UPDATES["citation_files"])
# ORCID work types that are talks or posters; they feed events, not publications.
EVENT_WORK_TYPES = set(UPDATES["event_work_types"])
TOPIC_SCORE = float(UPDATES["topic_score"])
WITHHELD = {f"http://www.wikidata.org/entity/{p}" for p in UPDATES["wikidata_withheld"]}
