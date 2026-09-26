"""Contributors and co-authors are identified from public records."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

from .config import (
    CITATION_FILES,
    DATA_DIR,
    GITHUB_USER,
    IGNORED_ACCOUNTS,
    ORCID_ID,
    PERSON,
    WITHHELD,
)
from .files import write_yaml_list
from .names import fold, full_name
from . import wikidata
from .net import Cache, fetch_json, github, github_raw, github_repo


def citation_authors(repo: str, path: str) -> list[dict[str, str]]:
    """Authors with ORCIDs are read from citation metadata."""
    text = github_raw(repo, path)
    if not text:
        return []
    try:
        data = yaml.safe_load(text) if path.endswith(".cff") else json.loads(text)
    except (yaml.YAMLError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    listed = data.get("authors") or data.get("creators") or data.get("author") or []
    listed = listed if isinstance(listed, list) else [listed]
    found = []
    for p in listed:
        if not isinstance(p, dict):
            continue
        orcid = str(p.get("orcid") or p.get("@id") or p.get("identifier") or "")
        match = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", orcid)
        name = p.get("name") or " ".join(v for v in (p.get("given-names") or p.get("givenName"), p.get("family-names") or p.get("familyName")) if v)
        if "," in name:  # "Family, Given"
            family, _, given = name.partition(",")
            name = f"{given.strip()} {family.strip()}"
        if match and name.strip():
            found.append({"name": name.strip(), "orcid": f"https://orcid.org/{match.group(0)}", "file": f"https://github.com/{repo}/blob/HEAD/{path}"})
    return found


def own_citation_files(login: str) -> list[tuple[str, str]]:
    """Citation files are found in the account’s repositories."""
    time.sleep(7)
    try:
        found = github(f"/search/code?q={urllib.parse.quote(f'orcid user:{login}')}&per_page=30")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    return sorted({(i["repository"]["full_name"], i["path"]) for i in found.get("items", []) if i["path"].rsplit("/", 1)[-1] in CITATION_FILES})


def openalex_ids(orcid: str) -> dict[str, str]:
    """Profile identifiers are fetched by ORCID."""
    try:
        ids = fetch_json(f"https://api.openalex.org/authors/orcid:{orcid.rsplit('/', 1)[-1]}").get("ids") or {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {}
    return {key: value for key, value in ids.items() if isinstance(value, str) and value.startswith("http")}


def orcid_identifiers(orcid: str) -> list[dict[str, str]]:
    """The external identifiers a person added to their ORCID record, as labeled links."""
    try:
        record = fetch_json(f"https://pub.orcid.org/v3.0/{orcid.rsplit('/', 1)[-1]}/external-identifiers")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    return [
        {"label": i["external-id-type"], "url": i["external-id-url"]["value"]}
        for i in record.get("external-identifier", []) if (i.get("external-id-url") or {}).get("value")
    ]


def own_links(own: dict, me: str) -> list[dict[str, str]]:
    """Additional profile links are shown in the page header."""
    site = yaml.safe_load((DATA_DIR.parent / "_config.yml").read_text(encoding="utf-8"))["canonical"]
    hosts = yaml.safe_load((DATA_DIR / "concepts.yml").read_text(encoding="utf-8"))["hosts"]
    shown = {me, site, *(p["url"] for p in PERSON["profiles"] if p.get("header"))}
    def key(url: str) -> str:
        """One key for the several addresses of one record (an entity IRI, its wiki page)."""
        parsed = urllib.parse.urlparse(url.lower().rstrip("/"))
        return parsed.netloc.removeprefix("www.") + "/" + parsed.path.rsplit("/", 1)[-1]

    shown = {key(u) for u in shown}
    candidates = [
        *({"url": u, "source": "", "kind": "profile"} for u in own["same_as"]),
        *({"url": p["url"], "source": "", "kind": "profile"} for p in PERSON["profiles"] if not p.get("header")),
        *({"url": a["url"], "source": a.get("source") or me, "kind": "profile"} for a in own["accounts"]),
        *({**p, "kind": "profile"} for p in own["profiles"]),
        *({**w, "kind": "website"} for w in own["websites"]),
    ]
    links, seen = [], set(shown)
    for c in candidates:
        if key(c["url"]) in seen:
            continue
        seen.add(key(c["url"]))
        host = urllib.parse.urlparse(c["url"]).netloc
        label = c.get("label") or hosts.get(host) or host.removeprefix("www.")
        source_host = urllib.parse.urlparse(c["source"]).netloc
        via = hosts.get(source_host) or source_host.removeprefix("www.")
        links.append({k: v for k, v in {"url": c["url"], "label": label, "kind": c["kind"], "via": via if via != label else ""}.items() if v})
    return sorted(links, key=lambda link: link["label"].casefold())


def update_collaborators(cv: dict, refresh: bool = False) -> None:
    """Identities are matched by declared links, Wikidata, citations, then names. Evidence is kept."""
    me = f"https://orcid.org/{ORCID_ID}"
    cache = Cache(refresh)
    software = cv.get("libraries", []) + cv.get("contributions", []) + cv.get("personal_tools", [])
    contributors: dict[str, list[str]] = {}
    for item in software:
        repo = github_repo(item.get("repository"))
        if not repo:
            continue
        try:
            listed = github(f"/repos/{repo}/contributors?per_page=100")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise RuntimeError(f"Could not fetch contributors of {repo}; collaborators.yml was not changed") from error
        if not isinstance(listed, list):
            raise ValueError(f"Invalid contributors response for {repo}; collaborators.yml was not changed")
        for account in listed:
            login = account.get("login", "")
            if account.get("type") == "Bot" or login.endswith("[bot]") or login.lower() in IGNORED_ACCOUNTS:
                continue
            contributors.setdefault(login, []).append(item["id"])

    publications = yaml.safe_load((DATA_DIR / "publications.yml").read_text(encoding="utf-8")) or []
    authors = sorted({
        a["orcid"] for row in publications
        for a in row.get("authors") or [] if a.get("orcid") and a["orcid"] != me
    })
    logins = sorted(login for login in contributors if login.lower() != GITHUB_USER.lower())
    def facts(qids: set[str]) -> list[dict[str, str]]:
        """Wikidata facts for these items: stale ones fetched together, then cached one by one."""
        stale = sorted(q for q in qids if not cache.fresh(f"wikidata:{q}"))
        fetched: dict[str, list[dict[str, str]]] = {q: [] for q in stale}
        for r in wikidata.facts(stale) if stale else []:
            fetched[r["item"].rsplit("/", 1)[-1]].append(r)
        for q, rows in fetched.items():
            cache.put(f"wikidata:{q}", rows)
        return [r for q in sorted(qids) for r in cache.entries[f"wikidata:{q}"]["value"]]

    github_items = {login: cache.get(f"github-item:{login.lower()}", wikidata.item, f"github:{login}", "P2037") for login in logins}
    by_github: dict[str, list[dict[str, str]]] = {}
    for r in facts({q for q in github_items.values() if q}):
        for login, qid in github_items.items():
            if qid and r["item"].endswith(f"/{qid}"):
                by_github.setdefault(login.lower(), []).append(r)
    orcids = sorted({ORCID_ID, *(o.rsplit("/", 1)[-1] for o in authors), *(r["orcid"] for rs in by_github.values() for r in rs if r.get("orcid"))})
    orcid_items = {o: cache.get(f"orcid-item:{o}", wikidata.item, f"orcid:{o}", "P496") for o in orcids}
    by_orcid: dict[str, list[dict[str, str]]] = {}
    for r in facts({q for q in orcid_items.values() if q}):
        for orcid, qid in orcid_items.items():
            if qid and r["item"].endswith(f"/{qid}"):
                by_orcid.setdefault(orcid, []).append(r)

    names_by_orcid: dict[str, set[tuple[str, str]]] = {}
    papers_by_orcid: dict[str, set[str]] = {}
    for row in publications:
        for a in row.get("authors") or []:
            if a.get("orcid") and a["orcid"] != me:
                names_by_orcid.setdefault(a["orcid"], set()).add((a.get("given", ""), a["family"]))
                if row.get("doi"):
                    papers_by_orcid.setdefault(a["orcid"], set()).add(row["doi"])
    software_papers = {i["id"]: set(i.get("publications") or []) for i in software}

    def profile(login: str) -> dict:
        try:
            user = github(f"/users/{login}")
        except (urllib.error.URLError, TimeoutError, ValueError):
            return {}
        return {"name": user.get("name"), "blog": user.get("blog")} if isinstance(user, dict) else {}

    def social(login: str) -> list[str]:
        try:
            accounts = github(f"/users/{login}/social_accounts")
        except (urllib.error.URLError, TimeoutError, ValueError):
            return []
        return [a.get("url", "") for a in accounts] if isinstance(accounts, list) else []

    def researcher_urls(orcid: str) -> list[str]:
        try:
            record = fetch_json(f"https://pub.orcid.org/v3.0/{orcid.rsplit('/', 1)[-1]}/researcher-urls")
        except (urllib.error.URLError, TimeoutError, ValueError):
            return []
        return [u["url"]["value"] for u in record.get("researcher-url", []) if u.get("url")]

    users = {login: cache.get(f"github-user:{login.lower()}", profile, login) for login in logins}
    by_login = {login.lower(): login for login in logins}
    identity: dict[str, tuple[str, str, str | None]] = {}  # login → (ORCID, how, evidence IRI)
    declared_accounts: dict[str, set[str]] = {}  # ORCID → GitHub profiles its record lists
    for orcid in names_by_orcid:
        for url in cache.get(f"orcid-urls:{orcid}", researcher_urls, orcid):
            match = re.match(r"https?://(?:www\.)?github\.com/([^/?#]+)/?$", url)
            if match:
                declared_accounts.setdefault(orcid, set()).add(match.group(1).lower())
                if match.group(1).lower() in by_login:
                    identity.setdefault(by_login[match.group(1).lower()], (orcid, "declared on ORCID record", orcid))
    tracked_citations = {}
    for item in software:
        repo = github_repo(item.get("repository"))
        if repo:
            tracked_citations[item["id"]] = []
            for path in CITATION_FILES:
                tracked_citations[item["id"]].extend(cache.get(f"citation:{repo}/{path}", citation_authors, repo, path))

    def fits_account(login: str, name: str) -> bool:
        shown = (users.get(login) or {}).get("name") or ""
        compact = re.sub(r"[^a-z0-9]", "", fold(login))
        return (bool(shown) and full_name(shown, "") == full_name(name, "")) or re.sub(r"[^a-z0-9]", "", fold(name)) == compact

    for login in logins:
        profile_url = f"https://github.com/{login}"
        readme = cache.get(f"github-readme:{login.lower()}", github_raw, f"{login}/{login}", "README.md") or ""
        declared = [f"https://orcid.org/{m}" for m in re.findall(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", readme)]
        for url in cache.get(f"github-social:{login.lower()}", social, login):
            declared += [f"https://orcid.org/{m}" for m in re.findall(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", url)]
        if len(set(declared)) == 1:
            identity.setdefault(login, (declared[0], "declared on GitHub profile", profile_url))
        if login in identity:
            continue
        cited = [
            a for repo, path in cache.get(f"github-citations:{login.lower()}", own_citation_files, login)
            for a in cache.get(f"citation:{repo}/{path}", citation_authors, repo, path)
        ]
        cited += [a for software in contributors[login] for a in tracked_citations.get(software, [])]
        fitting = {a["orcid"]: a["file"] for a in cited if fits_account(login, a["name"])}
        if len(fitting) == 1:
            orcid, file = next(iter(fitting.items()))
            identity[login] = (orcid, "declared in citation metadata", file)
        found = by_github.get(login.lower(), [])
        wikidata_orcid = next((r for r in found if r.get("orcid")), None)
        if wikidata_orcid:
            identity.setdefault(login, (f"https://orcid.org/{wikidata_orcid['orcid']}", "Wikidata", wikidata_orcid["item"].replace("https://", "http://")))
        if login in identity:
            continue
        compact = re.sub(r"[^a-z0-9]", "", fold(login))
        shown = (users.get(login) or {}).get("name") or ""
        fits = [
            orcid for orcid, names in names_by_orcid.items()
            if any(re.sub(r"[^a-z0-9]", "", fold(g + f)) == compact or (shown and full_name(shown, "") == full_name(g, f)) for g, f in names)
        ]
        papers = [doi for software in contributors[login] for doi in software_papers.get(software, set()) if fits and doi in papers_by_orcid.get(fits[0], set())]
        if len(fits) == 1 and papers:
            identity[login] = (fits[0], "inferred from name and co-authorship", f"https://doi.org/{papers[0]}")
            continue
        same_name = [orcid for orcid, names in names_by_orcid.items() if shown and any(full_name(shown, "") == full_name(g, f) for g, f in names)]
        if len(same_name) == 1:
            identity[login] = (same_name[0], "inferred from matching name", f"https://github.com/{login}")

    identifier_props = sorted({prop for rs in [*by_github.values(), *by_orcid.values()] for r in rs for prop, _ in r.get("ids") or []})
    stale_props = [prop for prop in identifier_props if not cache.fresh(f"wikidata-property:{prop}")]
    fetched_props = wikidata.formatters(stale_props) if stale_props else {}
    for prop in stale_props:
        cache.put(f"wikidata-property:{prop}", fetched_props.get(prop))
    formatters = {prop: cache.entries[f"wikidata-property:{prop}"]["value"] for prop in identifier_props if cache.entries[f"wikidata-property:{prop}"]["value"]}

    collaborators: dict[str, dict] = {}

    def person(key: str) -> dict:
        return collaborators.setdefault(key, {"id": key, "names": [], "same_as": [], "accounts": [], "websites": [], "profiles": [], "contributes": []})

    def add_profile(entry: dict, url: str, label: str | None, source: str) -> None:
        known = {u.lower().rstrip("/") for u in [entry["id"], *entry["same_as"], *(p["url"] for p in entry["profiles"]), *(a["url"] for a in entry["accounts"])]}
        if url.lower().rstrip("/") not in known:
            entry["profiles"].append({k: v for k, v in {"url": url, "label": label, "source": source}.items() if v})

    def add_wikidata(entry: dict, rows: list[dict]) -> None:
        for r in rows:
            item = r["item"].replace("https://", "http://")
            if item not in entry["same_as"]:
                entry["same_as"].append(item)
            if r.get("label") and {"name": r["label"], "source": item} not in entry["names"]:
                entry["names"].append({"name": r["label"], "source": item})
            if r.get("site") and {"url": r["site"], "source": item} not in entry["websites"]:
                entry["websites"].append({"url": r["site"], "source": item})
            account = f"https://github.com/{r['gh']}" if r.get("gh") else None
            if account and account.lower() not in {u.lower() for u in entry["same_as"]}:
                entry["same_as"].append(account)
            if account and account.lower() not in {a["url"].lower() for a in entry["accounts"]}:
                entry["accounts"].append({"url": account, "via": "Wikidata", "source": item})
            for prop, value in r.get("ids") or []:
                if prop in ("P496", "P2037") or prop not in formatters:
                    continue
                add_profile(entry, formatters[prop]["pattern"].replace("$1", urllib.parse.quote(str(value), safe="")), formatters[prop]["label"], item)

    for login in logins:
        profile_url = f"https://github.com/{login}"
        found = by_github.get(login.lower(), [])
        orcid, how, evidence = identity.get(login, (None, None, None))
        entry = person(orcid or profile_url)
        entry["contributes"] += [i for i in contributors[login] if i not in entry["contributes"]]
        if orcid:
            if profile_url not in entry["same_as"]:
                entry["same_as"].append(profile_url)
            entry["accounts"].append({"url": profile_url, "via": how, "source": evidence})
        user = users.get(login)
        if isinstance(user, dict):
            entry["names"].append({"name": user.get("name") or login, "source": profile_url})
            blog = (user.get("blog") or "").strip()
            if blog:
                blog = blog if blog.startswith("http") else f"https://{blog}"
                entry["websites"].append({"url": blog, "source": profile_url})
        add_wikidata(entry, found)
    for orcid in [a.rsplit("/", 1)[-1] for a in authors] + [o for o in orcids if f"https://orcid.org/{o}" in collaborators]:
        if by_orcid.get(orcid):
            add_wikidata(person(f"https://orcid.org/{orcid}"), by_orcid[orcid])
    # What co-authors' ORCID records say of them: their web links and external identifiers.
    for orcid in names_by_orcid:
        if orcid == me:
            continue
        entry = person(orcid)
        for url in cache.get(f"orcid-urls:{orcid}", researcher_urls, orcid):
            if "github.com" not in url and {"url": url, "source": orcid} not in entry["websites"]:
                entry["websites"].append({"url": url, "source": orcid})
        for identifier in cache.get(f"orcid-ids:{orcid}", orcid_identifiers, orcid):
            add_profile(entry, identifier["url"], identifier["label"], orcid)
    # What OpenAlex links to each identified person, and the links on contributors' GitHub profiles.
    for orcid in [*names_by_orcid, me]:
        ids = cache.get(f"openalex:{orcid}", openalex_ids, orcid)
        for key, url in sorted(ids.items()):
            if key != "orcid":
                add_profile(person(orcid), url, None, ids.get("openalex", url))
    for login in [*logins, GITHUB_USER]:
        orcid = me if login == GITHUB_USER else identity.get(login, (None, None, None))[0]
        profile_url = f"https://github.com/{login}"
        for url in cache.get(f"github-social:{login.lower()}", social, login):
            if "orcid.org" not in url:
                add_profile(person(orcid or profile_url), url, None, profile_url)
    # Accounts a co-author's ORCID record lists, even when they did not contribute here.
    for orcid, listed in declared_accounts.items():
        entry = person(orcid)
        for login in sorted(listed):
            url = f"https://github.com/{by_login.get(login, login)}"
            if url.lower() not in {u.lower() for u in entry["same_as"]}:
                entry["same_as"].append(url)
            if url.lower() not in {a["url"].lower() for a in entry["accounts"]}:
                entry["accounts"].append({"url": url, "via": "declared on ORCID record", "source": orcid})
    # The CV's subject: what their own ORCID record and Wikidata item link to.
    own = person(me)
    for url in cache.get(f"orcid-urls:{me}", researcher_urls, me):
        if {"url": url, "source": me} not in own["websites"]:
            own["websites"].append({"url": url, "source": me})
    for identifier in cache.get(f"orcid-ids:{me}", orcid_identifiers, me):
        add_profile(own, identifier["url"], identifier["label"], me)
    for r in by_orcid.get(ORCID_ID, []):
        item = r["item"].replace("https://", "http://")
        if item not in own["same_as"]:
            own["same_as"].append(item)
        if r.get("site") and {"url": r["site"], "source": item} not in own["websites"]:
            own["websites"].append({"url": r["site"], "source": item})
        for prop, value in r.get("ids") or []:
            if prop != "P496" and prop in formatters:
                add_profile(own, formatters[prop]["pattern"].replace("$1", urllib.parse.quote(str(value), safe="")), formatters[prop]["label"], item)
    github_account = f"https://github.com/{GITHUB_USER}"
    if github_account.lower() not in {a["url"].lower() for a in own["accounts"]}:
        own["accounts"].append({"url": github_account, "via": "cv.yml", "source": me})
    own["links"] = own_links(own, me)
    mine = [i for login, ids in contributors.items() if login.lower() == GITHUB_USER.lower() for i in ids]
    if mine:
        person(me)["contributes"] = mine
    items_of = {key: [s.rsplit("/", 1)[-1] for s in entry["same_as"] if "wikidata.org/entity/" in s] for key, entry in collaborators.items()}
    stale = sorted({q for qids in items_of.values() for q in qids if not cache.fresh(f"wikidata-statements:{q}")})
    for qid, rows in (wikidata.claims(stale) if stale else {}).items():
        cache.put(f"wikidata-statements:{qid}", rows)
    for key, qids in items_of.items():
        collaborators[key]["statements"] = [s for q in qids for s in cache.entries[f"wikidata-statements:{q}"]["value"] if s["property"] not in WITHHELD]
    cache.save()
    rows = [{k: v for k, v in p.items() if v} for p in sorted(collaborators.values(), key=lambda p: p["id"])]
    write_yaml_list(DATA_DIR / "collaborators.yml", rows)

    print(f"collaborators.yml: {len(rows)} collaborators ({len(logins)} contributors, {len(authors)} co-authors with ORCID)")
