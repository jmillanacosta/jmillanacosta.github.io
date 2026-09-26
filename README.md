# Javier Millán Acosta

Personal site as linked data built with Jekyll, this repository is also a template.

## The site

- 3 pages: a landing page (`/`), the CV (`/cv/`), and events (`/events/`).
- The CV as PDF downloadable from the CV page.
- **Linked data files** next to every page: `index.jsonld`, `index.ttl`, `index.nt`, and
  `index.rdf`, plus a dataset description at `/void.ttl`: the site's current data as mined by
  rdfsolve, with every class, the properties each uses, and the links between classes.
- **A page for every concept** in the data (organizations, places, software, publications,
  collaborators, events, and more), grouped by category (`/places/`, `/collaborators/`, …), and a
  `/content/` page that lists them all.
- **A schema page** (`/schema/`): a diagram of how the classes connect, and the schema as
  rdfsolve JSON, Pydantic models, SHACL, and LinkML.
- **A sitemap** with every page.

## Set it up

You need [uv](https://docs.astral.sh/uv/), Node 22 (`.nvmrc`), and Ruby with Bundler. Every
dependency is pinned in a lockfile: Python in `uv.lock` (from `pyproject.toml`; uv fetches the
Python version in `.python-version`), Node in `package-lock.json`, Ruby in `Gemfile.lock`. The
Makefile installs from them on first use and again whenever a lockfile changes, so any `make`
target works on a fresh clone.

To update a pin: `uv lock --upgrade-package rdfsolve` (or any package), `npm install
<package>@<version>`, `bundle update <gem>`; commit the lockfile.

1. Configure your identity:
   - `_config.yml`: the site address (`url`, `canonical`) and the title.
   - `_data/cv.yml`: the person (name, ORCID, job, profiles with a `GitHub` entry), and the
     content: experience, software, education, teaching, skills, and more.
   - `_data/events.yml`: talks, posters, workshops, and hackathons.
   - `_data/extra_publications.yml`: publications that are not on your ORCID record.
2. To do all of the next steps in one command: `make all` (install, data, images, site and PDF,
   checks). Or install first with `make setup` (other targets install what they need).
3. Fetch the data: `make data`. It fills the generated files in `_data` (publications,
   collaborators, software versions, usage figures). Set `GITHUB_TOKEN` first to avoid GitHub's
   limit of 60 calls an hour.
4. Build: `make images` draws the favicon and touch icon (your initials) and the social preview
   image (your name, job, and headline). `make pdf` builds the site and the PDF into `_site`.
   Draw the images before the build, because Jekyll copies them into `_site`.
5. `make serve` builds the site, then serves it at <http://127.0.0.1:4000> and rebuilds pages on
   changes. The generated pages and graphs are kept (`keep_files` in `_config.yml`); `make concepts`
   in a second terminal redoes them.
6. `make check` (needs Poppler for the PDF check).

To publish, push to GitHub and set Pages to deploy from GitHub Actions. Two workflows come with
the repository:

- `.github/workflows/jekyll-gh-pages.yml` builds and checks everything on each push, then
  deploys.
- `.github/workflows/update-cv-data.yml` runs the data update (`scripts/update_cv_data.py`) every
  Monday and commits what changed.

## Resolving identifiers and entry links

The sources below fill `_data` when `make data` runs, and every page
is rendered from `_data`.

**Publications.** Your ORCID record lists your works. Their authors come from Crossref (or
DataCite, for DOIs that Crossref does not register). Works missing from ORCID can be added in `_data/extra_publications.yml`.

**Resolve author identity** An author listed without an ORCID iD is identified, in this order:

1. someone whose own ORCID record claims the paper, if exactly one listed name fits their name;
2. the paper's author list on Wikidata, where each author has a position: the author at the
   same position, if the names fit (identified by their ORCID iD, or by the Wikidata item);
3. an author with an ORCID iD elsewhere in your publications with the same name.

**Collaborators.** Co-authors, and everyone who contributed to your software on GitHub, get a page.
A GitHub account is tied to a person's ORCID iD by the strongest evidence available:

1. the person says so, on their ORCID record or their GitHub profile;
2. Wikidata has both their GitHub name and their ORCID iD;
3. a citation file (`CITATION.cff`, `.zenodo.json`, `codemeta.json`) in their own repositories,
   or in your software's, lists them with their ORCID iD;
4. inferred: their GitHub name matches exactly one co-author (backed, where possible, by a paper
   that describes software they worked on).

Each tie records how it was made (`via`) and what shows it (`source`), in `_data/collaborators.yml`.
Then each person's websites and profiles elsewhere are collected from their ORCID record,
their Wikidata item (every identifier it has, turned into a link), OpenAlex, and their GitHub
profile. Every link keeps its source, and pages show it, as in "Website (Wikidata)". Only the
current, best-ranked statements on Wikidata are used.

**Your own links.** The same sources give the links shown behind "+N more" in the page header.
To add one, add it where it belongs (your ORCID record, your Wikidata item); it appears after the
next `make data`.

**Caching.** The slow lookups (Wikidata, ORCID records, GitHub profiles) are cached in
`scripts/cache/collaborators.json` and redone after `cache_days` (30, in `_config.yml`). `python scripts/update_cv_data.py collaborators
--refresh` (or `authors --refresh`) redoes them at once.

**From the data files to RDF.** The shapes of the RDF are not written by hand. They come from
the published vocabularies (schema.org, FOAF and DCMI Terms), read by
[rdfsolve](https://github.com/jmillanacosta/rdfsolve) in `scripts/shapes.py`. Each class in
`CLASSES` becomes a typed model. `schema/extensions.ttl` has the few uses of this site that the
vocabularies do not declare, each with a short reason (for example, a role on a person).

`scripts/build-graph.py` makes the records: one plain Python function for each kind of entry in
`_data`. Each value is checked against the shapes when the record is made. A value that is not
declared for its property stops the build with a short message. Each value gets the kind that
its property takes: a link, a year, a month, a date, a number or English text.

To add a new kind of record: add its class to `CLASSES` in `scripts/shapes.py`, write a function
in `scripts/build-graph.py`, and add the RDFa to the template. If the vocabularies do not declare
a property for the class, add one line to `schema/extensions.ttl`.

**Identifiers.** The site never invents an identifier for a thing. People, organizations,
places, software, and papers are identified by their registry IRIs (ORCID, ROR, Wikidata, DOI)
or their own web address. Things without one, such as most events, are blank nodes.

## Customize

| To change                                                             | Edit                                                             |
| --------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Site address and title                                                | `_config.yml`                                                    |
| How data updates behave (cache age, ignored accounts, citation files) | `_config.yml`, under `updates`                                   |
| Your details and all CV content                                       | `_data/cv.yml`                                                   |
| Events                                                                | `_data/events.yml`                                               |
| Publications missing from ORCID                                       | `_data/extra_publications.yml`                                   |
| Downloads offered on each page (PDF, RDF formats)                     | `_data/formats.yml`                                              |
| What the data states as RDF                                           | `scripts/build-graph.py`, `scripts/shapes.py`                    |
| Uses of properties that the vocabularies do not declare               | `schema/extensions.ttl`                                          |
| Concept pages: categories, labels, wording, order                     | `_data/concepts.yml`                                             |
| Usage figures shown for software                                      | `_data/stat_kinds.yml`                                           |
| Sections on a page, and what its linked data includes                 | the page's front matter (`sections`, `graph`)                    |
| Colors, fonts, spacing                                                | `assets/css/cv.css` (the tokens at the top)                      |
| Social preview image                                                  | your name, job, and `headline` in `_data/cv.yml`, then `make og` |
| Favicon and touch icon                                                | the initials of your name in `_data/cv.yml`, then `make icons`   |

Files written by the tools (do not edit by hand): `_data/publications.yml`, `_data/collaborators.yml`,
`_data/software.yml`, `_data/code_stats.yml`, `_data/events_generated.yml`, and
`scripts/cache/`.

## How the code is organized

- `_layouts/`, `_includes/`: the page templates, which render the data as RDFa.
- `scripts/shapes.py`: reads the vocabularies and `schema/extensions.ttl` with rdfsolve and
  makes the typed models.
- `scripts/build-graph.py`: makes the records of each page from `_data` and writes each graph as
  JSON-LD, with the graph of each page also in the page.
- `scripts/update_cv_data.py`: gets the data, with the modules in `scripts/cvdata/`
  (`publications`, `events`, `software`, `collaborators`, and the shared `config`, `net`, `names`).
  Wikidata is asked with SPARQL through rdfsolve.
- `scripts/build-rdf.py`: checks with Oxigraph that the RDFa and the JSON-LD of each page give the
  same graph, and writes the Turtle, N-Triples and RDF/XML files.
- `scripts/build-schema.py`: mines the built graphs with rdfsolve and writes the schema page and
  the VoID description.
- `scripts/build-concepts.py`: writes the concept, category and content pages from the merged
  graph, as set in `_data/concepts.yml`. Each concept page starts with how the owner is connected
  to it: the `story` paths, which are checked against the mined schema.
- `scripts/find-iris.py`, `scripts/check-iris.py`: find candidate IRIs for new concepts, and check
  that every IRI of the published graphs resolves.
- `scripts/build-pdf.mjs`, `scripts/build-og.mjs`: the PDF and the social preview image.
- `scripts/check-pdf.py`, `scripts/check-layout.mjs`: the checks of `make check` and of each
  deploy.
