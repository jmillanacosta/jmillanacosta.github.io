"""The locked Python dependencies are exported for GitHub's dependency graph."""

import datetime
import json
import os
import tomllib
from pathlib import Path
from urllib.parse import quote


def snapshot(lock, environment):
    packages = {p["name"]: p for p in lock["package"]}
    project = next(p for p in packages.values() if p["source"].get("virtual") == ".")
    direct = {p["name"] for p in project["dependencies"]}
    urls = {}
    for name, package in packages.items():
        if name != project["name"]:
            urls[name] = f"pkg:pypi/{quote(name)}@{quote(package['version'])}"
    resolved = {}
    for name, url in urls.items():
        package = packages[name]
        resolved[url] = {
            "package_url": url,
            "relationship": "direct" if name in direct else "indirect",
            "scope": "runtime",
            "dependencies": [urls[p["name"]] for p in package.get("dependencies", []) if p["name"] in urls],
        }
        if "git" in package["source"]:
            resolved[url]["metadata"] = {"git": package["source"]["git"]}
    return {
        "version": 0,
        "sha": environment["GITHUB_SHA"],
        "ref": environment["GITHUB_REF"],
        "job": {"id": environment["GITHUB_RUN_ID"], "correlator": "python-dependencies"},
        "detector": {"name": "uv-lock", "version": "1", "url": "https://docs.astral.sh/uv/"},
        "scanned": datetime.datetime.now(datetime.UTC).isoformat(),
        "manifests": {"uv.lock": {"name": "uv.lock", "file": {"source_location": "uv.lock"}, "resolved": resolved}},
    }


if __name__ == "__main__":
    lock = tomllib.loads(Path("uv.lock").read_text())
    print(json.dumps(snapshot(lock, os.environ)))
