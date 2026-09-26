"""Names are compared across different spellings and name parts."""

from __future__ import annotations

import re
import unicodedata

from .config import ORCID_ID, PERSON


def fold(s: str) -> str:
    """Accents are removed and text is changed to lower case."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()


def is_me(given: str, family: str, orcid: str | None) -> bool:
    """The CV owner is matched by ORCID, or by name when no ORCID is given."""
    if orcid:
        return orcid.rstrip("/").endswith(ORCID_ID)
    full = fold(f"{given} {family}").split()
    wanted = fold(PERSON["family_name"]).split()
    first = fold(PERSON["given_name"]).split()[:1]
    return all(part in full for part in wanted) and all(part in full for part in first)


def given_parts(given: str) -> list[str]:
    return [part for part in re.split(r"[\s.\-]+", fold(given)) if part]


def compatible(a: str, b: str) -> bool:
    """Given names are compared in order; matching initials are accepted."""
    parts_a, parts_b = given_parts(a), given_parts(b)
    if not parts_a or not parts_b:
        return False
    for x, y in zip(parts_a, parts_b, strict=False):
        if (len(x) == 1 or len(y) == 1) and x[0] != y[0]:
            return False
        if len(x) > 1 and len(y) > 1 and x != y:
            return False
    return True


def full_name(given: str, family: str) -> str:
    """Name splits, punctuation and bracketed nicknames are ignored."""
    return " ".join(given_parts(re.sub(r"\([^)]*\)", " ", f"{given} {family}")))


def names_fit(given: str, family: str, full: str) -> bool:
    """Full names are compared, with surname parts and initials accepted."""
    if full_name(given, family) == full_name(full, ""):
        return True
    parts, surname = given_parts(full), given_parts(family)
    if not surname or not all(part in parts for part in surname):
        return False
    rest = [part for part in parts if part not in surname]
    return not rest or not given_parts(given) or compatible(given, " ".join(rest))
