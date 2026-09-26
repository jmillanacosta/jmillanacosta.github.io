"""Comparing person names across sources that write and split them differently."""

from __future__ import annotations

import re
import unicodedata

from .config import ORCID_ID, PERSON


def fold(s: str) -> str:
    """Strip accents and lowercase, for tolerant name matching."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()


def is_me(given: str, family: str, orcid: str | None) -> bool:
    """This CV's subject: by ORCID, else by name, allowing for registries that split the family
    name differently (every part of it present, and the given name's first word)."""
    if orcid:
        return orcid.rstrip("/").endswith(ORCID_ID)
    full = fold(f"{given} {family}").split()
    wanted = fold(PERSON["family_name"]).split()
    first = fold(PERSON["given_name"]).split()[:1]
    return all(part in full for part in wanted) and all(part in full for part in first)


def given_parts(given: str) -> list[str]:
    return [part for part in re.split(r"[\s.\-]+", fold(given)) if part]


def compatible(a: str, b: str) -> bool:
    """Whether two given names can be the same person's: part by part, in order, words must be
    equal and an initial must match the word's first letter ("E." and "Egon" fit "Egon L")."""
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
    """A name with the given/family split, hyphens, dots, and parenthesized nicknames ignored:
    registries split names differently ("Jose Emilio Labra" + "Gayo", "Jose Emilio" +
    "Labra-Gayo"), and profiles add nicknames ("Friederike (Freddie) Ehrhart")."""
    return " ".join(given_parts(re.sub(r"\([^)]*\)", " ", f"{given} {family}")))


def names_fit(given: str, family: str, full: str) -> bool:
    """Whether a name written in one piece ("Thomas E. Exner") can be this given and family name:
    the whole name matches, or every family-name part is in it and the rest fits the given name."""
    if full_name(given, family) == full_name(full, ""):
        return True
    parts, surname = given_parts(full), given_parts(family)
    if not surname or not all(part in parts for part in surname):
        return False
    rest = [part for part in parts if part not in surname]
    return not rest or not given_parts(given) or compatible(given, " ".join(rest))
