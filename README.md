# My personal site built with rdfsolve

A Jekyll template for a personal site, CV, publications and events, in which all content is
linked data. The linked data is made and checked with
[rdfsolve](https://github.com/jmillanacosta/rdfsolve).
[My site](https://jmillanacosta.github.io/) is included as an example.

## The site

- A CV site: home page, CV, events, publications, and a PDF of the CV.
- One page for each thing that is linked to the owner of the site: collaborators, software,
  publications, organizations, places, events and topics (for example
  `/collaborators/<name>/`). Each page says how the thing is connected to the owner.
- The RDF of every page, as JSON-LD and RDFa in the page, and as Turtle, N-Triples and
  RDF/XML files next to it (`index.ttl`, `index.nt`, `index.jsonld`).
- A description of the data of the whole site: the schema at `/schema/`, the VoID
  description at `/.well-known/void`, and SHACL shapes at `/.well-known/shacl`.

GitHub Pages has no content negotiation. Each page therefore links to its RDF with FAIR
Signposting (`<link rel="describedby">`, `type`, `author` and `cite-as`), so that programs
find the data of a page from the page itself.

## rdfsolve usage

1. **Wikidata is read with a mined schema.** The schema of Wikidata is mined only around
   the Wikidata items that the site data links to, in the main graph and in the graph of
   scholarly works. Typed records are made from that schema, so that an event has
   `start_time` and `organizer`, and a person has `orcid_id`. A change in Wikidata is found
   at the next mining run.
2. **The RDF is checked when it is made.** The records of the site are made with models
   that rdfsolve generates from schema.org, FOAF and Dublin Core. A property or a value
   that the vocabularies do not allow stops the build.
3. **The site is described.** The graphs of the site are mined like any other RDF source.
   The schema, the SHACL shapes, the VoID description and the downloads at `/schema/` come
   from this.
4. **The connections are checked.** The sentences of the concept pages ("We are co-authors
   of…") come from property paths in `_data/concepts.yml`. Each path is checked against the
   mined schema of the site before it is used.

No axioms are added to schema.org or FOAF. Where these vocabularies do not declare a value
that the site gives (the Role pattern of schema.org, the CodeMeta properties of source code,
authors in their order), the application profile `schema/profile.ttl` states it in SHACL
shapes that belong to the site.

## Make site

`make all` runs the following steps in order:

1. `make setup`: the dependencies are installed from the lockfiles (`uv.lock`,
   `Gemfile.lock`, `package-lock.json`). The rdfsolve revision is kept in `uv.lock`.
2. `make wikidata-schema` (`scripts/mine-wikidata.py`): the Wikidata items in `_data` are
   collected, and the schema around them is mined into `schema/wikidata.schema.json` and
   `schema/wikidata-scholarly.schema.json`. The changes since the last run are listed. When
   a field that the site reads (`FIELDS` in `scripts/cvdata/wikidata.py`) is no longer in
   Wikidata, the run fails and the new schema is kept in `output/` for review.
3. `make data` (`scripts/update_cv_data.py`): `_data` is refreshed from ORCID, Crossref,
   DataCite, Zenodo, PyPI, GitHub, OpenAlex and Wikidata. Slow lookups are cached in
   `scripts/cache`.
4. `make images`: the icons and the preview image are drawn from `_data/cv.yml`.
5. `make site`: the pages are built by Jekyll, then `make concepts` runs:
   - `scripts/build-graph.py`: the RDF of each page is made from `_data`, with the
     generated models.
   - `scripts/build-rdf.py`: the RDFa and the JSON-LD of each page are compared, and the
     other RDF formats are written.
   - `scripts/build-schema.py`: the graphs of the site are mined, and the schema page, the
     SHACL shapes and the VoID description are written.
   - `scripts/build-concepts.py`: one page is written for each thing in the graph, with
     its RDF and its links.
6. `make pdf`: the PDF of the CV is printed from the CV page.
7. `make check`: the PDF, the layout at six widths, and the formatting are checked.

Each step can also be run alone. For a preview of the included data, `make site` and
`make serve` are enough (<http://127.0.0.1:4000>); no data refresh or token is needed.
[uv](https://docs.astral.sh/uv/), Node 22, Ruby with Bundler, and Make are required.
Poppler is required for the PDF checks.

## Using this template

If you use GitHub pages, make a copy with the **Use this template** button. A repository named
`<username>.github.io` is published at the domain root. The example content is replaced in
these files:

| Content                                                 | File                           |
| ------------------------------------------------------- | ------------------------------ |
| Site address, title and description                     | `_config.yml`                  |
| Name, ORCID, GitHub profile, work, education and skills | `_data/cv.yml`                 |
| Events and their host institutions                      | `_data/events.yml`             |
| Publications not listed on ORCID                        | `_data/extra_publications.yml` |
| PDF filename and other downloads                        | `_data/formats.yml`            |
| Page labels, categories and connection sentences        | `_data/concepts.yml`           |
| Colours, fonts and spacing                              | `assets/css/cv.css`            |

`canonical` is the full site address, with a trailing slash. `person.iri` is the ORCID URL
of the person. Organization keys, such as `works_for`, refer to entries in `organizations`.
Unused list sections are left as `[]`. After an edit, `make all` is run; a `GITHUB_TOKEN`
in the environment is recommended for the data refresh.

Further changes:

- **New shapes**: add its schema.org class to `CLASSES` in
  `scripts/shapes.py`, and a record function to `scripts/build-graph.py`. If the class
  needs a value that schema.org does not declare for it, add a shape to
  `schema/profile.ttl`.
- **Other Wikidata fields**: add the field to `FIELDS` in `scripts/cvdata/wikidata.py`,
  and `make wikidata-schema` is run. The field names come from the English labels of the
  Wikidata properties.
- **Other connection sentences**: a path and its sentences can be added under `story` in
  `_data/concepts.yml`. A path that uses a property the site does not have stops the build.
- **Identifiers for new terms**: write
  candidates from Wikidata, OLS, ROR and ESCO running `uv run --locked python scripts/find-iris.py`, results go to `output/iri-candidates.yml` for review.

## Publication

If you use GitHub pages: **Settings > Pages**, set source to **GitHub Actions**. The site is built
and checked on a push to `main`. The data and the Wikidata schemas are refreshed by a weekly
workflow, or manually dispatched. GitHub Actions must be enabled in a new copy.
