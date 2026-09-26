"""Typed records are generated from published vocabularies and local extensions."""

import urllib.request
from pathlib import Path

from rdfsolve.api import Client
from rdfsolve.schema_models import MinedSchema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "https://schema.org/"
CACHE = ROOT / "scripts/cache"
VOCABULARIES = {
    "schemaorg-30.1.ttl": "https://schema.org/version/30.1/schemaorg-current-https.ttl",
    "foaf-0.99.rdf": "http://xmlns.com/foaf/spec/20140114.rdf",
    "dcterms-2020-01-20.ttl": "https://www.dublincore.org/specifications/dublin-core/dcmi-terms/dublin_core_terms.ttl",
}
EXTENSIONS = ROOT / "schema/extensions.ttl"

CLASSES = [
    "ProfilePage", "MediaObject",
    "Person", "Organization", "CollegeOrUniversity", "ResearchOrganization", "Corporation",
    "GovernmentOrganization", "Place", "VirtualLocation", "PostalAddress", "PropertyValue",
    "EmployeeRole", "OrganizationRole", "PerformanceRole", "Role",
    "ScholarlyArticle", "Report", "Periodical", "WebSite", "WebPage", "SoftwareSourceCode", "InteractionCounter",
    "Event", "Project", "ResearchProject", "Grant",
    "Course", "CourseInstance", "EducationalOccupationalProgram",
    "EducationalOccupationalCredential", "Occupation", "Language", "Taxon", "DefinedTerm", "Thing",
]


def vocabularies() -> list[Path]:
    """Published vocabularies are cached locally."""
    for name, url in VOCABULARIES.items():
        if not (CACHE / name).exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, CACHE / name)
    return [*(CACHE / name for name in VOCABULARIES), EXTENSIONS]


def shapes() -> MinedSchema:
    """The declared properties and values of each class in CLASSES."""
    return MinedSchema.from_vocabulary(vocabularies(), [SCHEMA + name for name in CLASSES])


def models() -> Client:
    """Records are checked against the generated rdfsolve models."""
    return Client(shapes(), contract=True)
