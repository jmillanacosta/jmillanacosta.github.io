# Javier Millán Acosta

Personal CV, built with Jekyll.

## Development

Requires Ruby, Bundler and Node.js 22 or later.

```sh
bundle install
npm ci
npx playwright install chromium
bundle exec jekyll serve --host 127.0.0.1 --port 4100
```

## PDF

```sh
bundle exec jekyll build
node scripts/build-pdf.mjs
node scripts/check-layout.mjs
python3 scripts/check-pdf.py
```

The PDF uses the site's print stylesheet and is saved in `output/pdf/` and `_site/output/pdf/`. On Linux, `npx playwright install --with-deps chromium` also installs the required system libraries. PDF validation requires Poppler.
