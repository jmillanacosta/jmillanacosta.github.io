# A personal site built with rdfsolve

A Jekyll template for a personal site, CV, publications and events. The linked data is
built and checked with [rdfsolve](https://github.com/jmillanacosta/rdfsolve).
[Javier Millán Acosta's site](https://jmillanacosta.github.io/) is included as an example.

The same records are published as RDFa, JSON-LD, Turtle, N-Triples and RDF/XML.
Pages are generated for people, institutions, software, topics and their connections.
At `/schema/`, the schema can be viewed as a diagram or downloaded as rdfsolve JSON,
Pydantic models, SHACL and LinkML. A dataset description is available at `/void.ttl`.

## Local preview

[uv](https://docs.astral.sh/uv/), Node 22, Ruby with Bundler, and Make are required.
Python and all dependencies are pinned. The rdfsolve revision is kept in `uv.lock`.

```sh
make site
make serve
```

The included data is used for the preview at <http://127.0.0.1:4000>.
No data refresh or API token is needed. After a data edit, the linked pages can be rebuilt
with `make concepts` in another terminal.

## Your site

A copy can be created with GitHub's **Use this template** button, or by copying this repository.
A repository named `<username>.github.io` is intended for publication at the domain root.

| Content                                                 | File                           |
| ------------------------------------------------------- | ------------------------------ |
| Site address, title and description                     | `_config.yml`                  |
| Name, ORCID, GitHub profile, work, education and skills | `_data/cv.yml`                 |
| Events and their host institutions                      | `_data/events.yml`             |
| Publications not listed on ORCID                        | `_data/extra_publications.yml` |
| PDF filename and other downloads                        | `_data/formats.yml`            |
| Page labels, categories and connections                 | `_data/concepts.yml`           |
| Colours, fonts and spacing                              | `assets/css/cv.css`            |

`canonical` is the full site address, with a trailing slash. `person.iri` is the person's
ORCID URL. Organization keys, such as `works_for`, refer to entries in `organizations`.
Unused list sections can be left as `[]`. The example content should be replaced before publication.

After the personal data has been edited:

```sh
export GITHUB_TOKEN=...   # An authenticated data refresh is recommended.
make data
make images
make pdf
make check
```

Publications, collaborators, repository metadata and usage figures are fetched from public
sources. Generated data is stored in `_data`; slow lookups are cached in `scripts/cache`.
Names and links are kept with their sources. A failed contributor request stops the refresh
before the collaborator file is replaced.

The icons and preview image are generated from the name and headline in `cv.yml`.
The PDF is written to `_site/cv/` and `output/cv/`. Poppler is required for its checks.
`make all` is available for a full refresh and build.

## Publication

In **Settings → Pages**, the build source should be set to **GitHub Actions**.
The site is built and checked on a push to `main`. Data is refreshed by the weekly workflow,
which can also be run manually. GitHub Actions must be enabled in a new copy of the repository.

## The data model

The published vocabularies are read by rdfsolve in `scripts/shapes.py`. Typed records are
created in `scripts/build-graph.py`, with one function per kind of content. Invalid values
are rejected before publication. RDFa and JSON-LD are generated from those same records;
no RDF attributes are maintained in the display templates.

For a new kind of content, its class is added to `CLASSES` in `scripts/shapes.py` and its
record function to `scripts/build-graph.py`. Extra vocabulary declarations are kept in
`schema/extensions.ttl`. Concept pages and exports are then generated from the graph.
Existing identifiers are used where available; other resources are represented as blank nodes.

Wikidata is read the same way, from schemas that rdfsolve mines. `make wikidata-schema` reads
every statement of the Wikidata items that the site data links, and of the author statements
of works, in the main graph and in the graph of scholarly works of the Wikidata Query Service
(`scripts/mine-wikidata.py`). It writes `schema/wikidata.schema.json` and
`schema/wikidata-scholarly.schema.json`, and says what changed. rdfsolve generates typed records
from these schemas (`scripts/cvdata/wikidata.py`): the fields are named after the English labels
of the properties, so an event has `start_time` and `organizer`, and a person has `orcid_id`.
The run fails when a field that the site reads (`FIELDS`) is no longer in the data; the new
schema is then kept in `output/` for review. Items are found by their identifiers (DOI, ORCID,
GitHub account) with `identify`. Publication subjects, OpenAlex topics and event details can be
refreshed with `uv run --locked python scripts/update_cv_data.py wikidata` and `... topics`.

| Command                                       | Result                                                        |
| --------------------------------------------- | ------------------------------------------------------------- |
| `make setup`                                  | All build dependencies are installed from the lockfiles       |
| `make site`                                   | HTML, linked data, concept pages and schema exports are built |
| `make serve`                                  | A local preview is served                                     |
| `make data`                                   | Public data is refreshed                                      |
| `make wikidata-schema`                        | The Wikidata schemas are mined again, and changes are listed  |
| `make images`                                 | Icons and the social preview are generated                    |
| `make pdf`                                    | The site and CV PDF are built                                 |
| `make check`                                  | The PDF, layout and formatting are checked                    |
| `uv run --locked python scripts/find-iris.py` | Identifier candidates are saved for review                    |
