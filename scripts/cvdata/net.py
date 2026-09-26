"""Public source requests and cached lookups."""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import CACHE_DAYS, CACHE_FILE, HEADERS

# IPv4 is preferred when IPv6 routes are unavailable.
_getaddrinfo = socket.getaddrinfo


def ipv4_first(*args, **kwargs):
    addresses = _getaddrinfo(*args, **kwargs)
    return [address for address in addresses if address[0] == socket.AF_INET] or addresses


socket.getaddrinfo = ipv4_first


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_optional(url: str) -> dict | None:
    """Unavailable usage figures are omitted."""
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


def github(path: str) -> Any:
    """GitHub requests are authenticated when GITHUB_TOKEN or GH_TOKEN is set."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {**HEADERS, "Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com{path}", headers=headers), timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def github_raw(repo: str, path: str) -> str | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/HEAD/{path}", headers=HEADERS), timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def github_repo(url: str | None) -> str | None:
    match = re.match(r"https://github\.com/([^/]+/[^/#?]+)", url or "")
    return match.group(1).removesuffix(".git") if match else None


class Cache:
    def __init__(self, refresh: bool = False):
        self.entries: dict[str, dict] = {} if refresh or not CACHE_FILE.exists() else json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        self.today = time.strftime("%Y-%m-%d")

    def fresh(self, key: str) -> bool:
        entry = self.entries.get(key)
        return bool(entry) and time.mktime(time.strptime(entry["checked"], "%Y-%m-%d")) > time.time() - CACHE_DAYS * 86400

    def put(self, key: str, value: Any) -> None:
        self.entries[key] = {"checked": self.today, "value": value}

    def get(self, key: str, compute: Any, *args: Any, **kwargs: Any) -> Any:
        """A value is fetched again when missing or stale."""
        if not self.fresh(key):
            self.put(key, compute(*args, **kwargs))
        return self.entries[key]["value"]

    def save(self) -> None:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(self.entries, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
