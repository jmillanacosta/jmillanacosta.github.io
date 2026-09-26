"""Package versions, repository metadata and usage figures."""

from __future__ import annotations

import json
import re
import tomllib
import urllib.error
from pathlib import PurePosixPath

import yaml

from .config import DATA_DIR
from .files import write_yaml_list
from . import wikidata
from .net import Cache, fetch_json, fetch_optional, github, github_raw, github_repo, live
from .wikidata import WD

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


def skill_identifiers(iris):
    """The PyPI projects, npm packages and GitHub topics that Wikidata gives for skills."""
    rows = []
    for iri, record in wikidata.read(iris).items():
        for scheme, values in (("pypi", record.pypi_project), ("npm", record.npm_package), ("topic", record.github_topic)):
            rows += [{"item": iri, "scheme": scheme, "value": str(value)} for value in sorted(values)]
    return rows


def skill_keys(cv: dict) -> dict[str, set[str]]:
    terms = {iri: name for name, iri in cv.get("skill_terms", {}).items() if iri.startswith(WD)}
    cache = Cache()
    rows = cache.get("skill-identifiers:" + "|".join(sorted(terms)), skill_identifiers, tuple(sorted(terms)))
    cache.save()
    keys = {f"topic:{normal(name)}": {name} for name in cv.get("skill_terms", {})}
    for r in rows:
        keys.setdefault(f"{r['scheme']}:{normal(r['value'])}", set()).add(terms[r["item"]])
    return keys


def packages(repo: str, path: str) -> list[str]:
    text = github_raw(repo, path)
    if text is None:
        raise ValueError(f"Could not read {repo}/{path}")
    name = PurePosixPath(path).name
    if name == "package.json":
        data = json.loads(text or "{}")
        return [f"npm:{name}" for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies") for name in data.get(key) or {}]
    if name == "pyproject.toml":
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
    tree = github(f"/repos/{repo}/git/trees/{info['default_branch']}?recursive=1")
    if tree.get("truncated"):
        raise ValueError(f"Incomplete file listing for {repo}")
    paths = []
    for entry in tree["tree"]:
        path = PurePosixPath(entry["path"])
        if entry["type"] == "blob" and not {"node_modules", "vendor", ".venv"}.intersection(path.parts):
            paths.append(path)
    requirements = [package for path in paths if path.name in MANIFESTS for package in packages(repo, str(path))]
    if any(path.name == "Dockerfile" or path.name.startswith("Dockerfile.") for path in paths):
        requirements.append("topic:Docker")
    if any(path.parts[:2] == (".github", "workflows") and path.suffix in (".yml", ".yaml") for path in paths):
        requirements.append("topic:GitHub Actions")
    blog = (owner.get("blog") or "").strip()
    return {
        "owner": owner["html_url"],
        "owner_website": blog if not blog or blog.startswith("http") else f"https://{blog}",
        "languages": [f"topic:{language}" for language in github(f"/repos/{repo}/languages")],
        "topics": [f"topic:{topic}" for topic in info.get("topics", [])],
        "packages": requirements,
    }


def update_repositories(cv: dict, refresh: bool = False) -> None:
    cache = Cache(refresh)
    keys = skill_keys(cv)
    rows = []
    for item in [*cv.get("libraries", []), *cv.get("contributions", []), *cv.get("personal_tools", [])]:
        repo = github_repo(item.get("repository"))
        if not repo:
            continue
        try:
            found = cache.get(f"repository:{repo}", repository, repo)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise RuntimeError(f"Could not fetch {repo}; repositories.yml was not changed") from error
        skills = {
            field: sorted({name for key in found[evidence] for name in keys.get(normal(key), ())})
            for field, evidence in EVIDENCE.items()
        }
        rows.append({k: v for k, v in {"id": item["id"], "owner": found["owner"], "owner_website": found["owner_website"], **skills}.items() if v})
    write_yaml_list(DATA_DIR / "repositories.yml", rows)
    cache.save()

    print(f"repositories.yml: owners and skills of {len(rows)} repositories")
