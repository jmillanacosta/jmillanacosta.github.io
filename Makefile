UV ?= uv
PYTHON ?= $(UV) run --locked python
NODE ?= node
BUNDLE ?= bundle

GEMS := .bundle/.installed
NODE_MODULES := node_modules/.installed
BROWSER := node_modules/.chromium

.PHONY: help all setup data images icons og site pdf serve concepts check clean

help:
	@echo "make all       do everything in order: setup, data, images, site and PDF, check"
	@echo "make setup     install everything from the lockfiles (uv.lock, package-lock.json, Gemfile.lock)"
	@echo "make data      refresh _data from ORCID, Crossref, Zenodo, PyPI, GitHub, Wikidata, OpenAlex"
	@echo "make site      build _site: pages, linked data, concept pages, schema page, VoID"
	@echo "make pdf       build the site, then the CV as PDF"
	@echo "make images    draw the favicon, the touch icon, and the social preview image"
	@echo "make icons     draw the favicon and the touch icon from the initials in _data/cv.yml"
	@echo "make og        draw the social preview image"
	@echo "make serve     build the site, then serve it locally, rebuilding pages on changes"
	@echo "make concepts  redo linked data, concept pages, the schema page, and VoID after a rebuild"
	@echo "make check     check the PDF, the layout, and formatting"
	@echo "make clean     remove the build and the installed environments"

# One recipe line per step, so the order holds also with make -j.
all: setup
	$(MAKE) data
	$(MAKE) images
	$(MAKE) pdf
	$(MAKE) check

setup: $(GEMS) $(BROWSER)
	$(UV) sync --locked

$(GEMS): Gemfile Gemfile.lock
	$(BUNDLE) install
	@mkdir -p $(@D) && touch $@

$(NODE_MODULES): package.json package-lock.json
	npm ci
	@touch $@

$(BROWSER): $(NODE_MODULES)
	npx playwright install chromium
	@touch $@

data:
	$(PYTHON) scripts/update_cv_data.py

site: $(GEMS)
	$(BUNDLE) exec jekyll build
	$(MAKE) concepts

concepts:
	$(PYTHON) scripts/build-graph.py
	$(PYTHON) scripts/build-rdf.py
	$(PYTHON) scripts/build-schema.py
	$(PYTHON) scripts/build-concepts.py

pdf: site $(BROWSER)
	$(NODE) scripts/build-pdf.mjs

# The images are written into the source tree; build the site after them.
images: icons og

icons: $(BROWSER)
	$(NODE) scripts/build-icons.mjs

og: $(BROWSER)
	$(NODE) scripts/build-og.mjs

# keep_files in _config.yml keeps the generated pages and graphs when Jekyll rebuilds.
serve: site
	$(BUNDLE) exec jekyll serve --livereload

check: $(BROWSER)
	$(PYTHON) scripts/check-pdf.py
	$(NODE) scripts/check-layout.mjs
	npx prettier . --check

clean:
	rm -rf _site .jekyll-cache .venv node_modules $(GEMS) output
