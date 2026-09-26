"""The shapes of the RDF data of this site.

The published vocabularies below and schema/extensions.ttl are read by rdfsolve. Each class in
CLASSES becomes a typed model. To use one more class, add its name to CLASSES.
"""

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
    """The files of the published vocabularies and of the extensions of this site.

    A vocabulary is downloaded once into scripts/cache.
    """
    for name, url in VOCABULARIES.items():
        if not (CACHE / name).exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, CACHE / name)
    return [*(CACHE / name for name in VOCABULARIES), EXTENSIONS]


def shapes() -> MinedSchema:
    """The declared properties and values of each class in CLASSES."""
    return MinedSchema.from_vocabulary(vocabularies(), [SCHEMA + name for name in CLASSES])


def models() -> Client:
    """A client that writes records of the shapes. Each record is checked against the shapes."""
    return Client(shapes(), contract=True)
