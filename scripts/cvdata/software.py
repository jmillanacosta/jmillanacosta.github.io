"""Package versions and usage figures for the listed software."""

from __future__ import annotations

import json
import re
import tomllib
import urllib.error

import yaml

from .config import DATA_DIR
from .files import write_yaml_list
from .net import WD, fetch_json, fetch_optional, github, github_raw, github_repo, items, live, wikidata_rows

MANIFESTS = ("pyproject.toml", "package.json", "requirements.txt")
EVIDENCE = {"programmingLanguage": "languages", "softwareRequirements": "packages", "about": "topics"}


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
        repo = github_repo(item.get("repository"))
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


def normal(name: str) -> str:
    return re.sub(r"[-_.\s]+", "-", name).lower()


def skill_keys(cv: dict) -> dict[str, set[str]]:
    terms = {iri.removeprefix(WD): name for name, iri in cv.get("skill_terms", {}).items() if iri.startswith(WD)}
    rows = wikidata_rows(f"""SELECT ?item ?scheme ?value WHERE {{
  VALUES ?item {{ {items(sorted(terms))} }}
  VALUES (?direct ?scheme) {{ (wdt:P5568 "pypi") (wdt:P8262 "npm") (wdt:P9100 "topic") }}
  ?item ?direct ?value
}}""")
    keys: dict[str, set[str]] = {}
    for r in rows:
        keys.setdefault(f"{r['scheme']}:{normal(r['value'])}", set()).add(terms[r["item"].removeprefix(WD)])
    return keys


def packages(repo: str, path: str) -> list[str]:
    text = github_raw(repo, path) or ""
    if path == "package.json":
        data = json.loads(text or "{}")
        return [f"npm:{name}" for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies") for name in data.get(key) or {}]
    if path == "pyproject.toml":
        data = tomllib.loads(text)
        project = data.get("project", {})
        groups = [*project.get("optional-dependencies", {}).values(), *data.get("dependency-groups", {}).values()]
        specs = [*project.get("dependencies", []), *(spec for group in groups for spec in group if isinstance(spec, str))]
    else:
        specs = [line for line in text.splitlines() if not line.strip().startswith(("#", "-"))]
    return [f"pypi:{match.group(1)}" for spec in specs if (match := re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec))]


def repository(repo: str) -> dict:
    info = github(f"/repos/{repo}")
    owner = github(f"/users/{info['owner']['login']}")
    blog = (owner.get("blog") or "").strip()
    return {
        "owner": owner["html_url"],
        "owner_website": blog if not blog or blog.startswith("http") else f"https://{blog}",
        "languages": [f"topic:{language}" for language in github(f"/repos/{repo}/languages")],
        "topics": [f"topic:{topic}" for topic in info.get("topics", [])],
        "packages": [package for path in MANIFESTS for package in packages(repo, path)],
    }


def update_repositories(cv: dict) -> None:
    keys = skill_keys(cv)
    rows = []
    for item in [*cv.get("libraries", []), *cv.get("contributions", []), *cv.get("personal_tools", [])]:
        repo = github_repo(item.get("repository"))
        if not repo:
            continue
        try:
            found = repository(repo)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            print(f"  skipped the repository {repo}: {error}")
            continue
        skills = {
            field: sorted({name for key in found[evidence] for name in keys.get(normal(key), ())})
            for field, evidence in EVIDENCE.items()
        }
        rows.append({k: v for k, v in {"id": item["id"], "owner": found["owner"], "owner_website": found["owner_website"], **skills}.items() if v})
    (DATA_DIR / "repositories.yml").write_text(
        "# Written by scripts/update_cv_data.py: the owner of each listed repository, and the skills\n"
        "# (cv.yml skill_terms) its languages, topics and packages name, by the identifiers Wikidata gives.\n"
        + yaml.safe_dump(rows, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    print(f"repositories.yml: owners and skills of {len(rows)} repositories")
