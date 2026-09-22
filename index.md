---
title: Javier Millán Acosta - CV
description: CV page for Javier Millán Acosta, a doctoral researcher in biomedical knowledge graphs, ontologies, and RDF tooling at Maastricht University (Department of Translational Genomics).
layout: default
---

<div class="cv" itemscope itemtype="https://schema.org/Person" markdown="1">

<header class="cv-header" id="top" markdown="1">

# <span itemprop="name">Javier Millán Acosta</span>

<span itemprop="jobTitle">Doctoral Researcher</span>, <span itemprop="worksFor" itemscope itemtype="https://schema.org/CollegeOrUniversity"><span itemprop="name">Maastricht University</span></span> - Department of Translational Genomics (TGX) · <span itemprop="address">Maastricht, Netherlands</span>

I develop research software for biomedical data integration, from identifier reconciliation and RDF schema discovery to graph-database services and web applications. My doctoral research examines how life sciences datasets can be connected and queried, and how changes in their identifiers affect downstream analyses.

<nav class="contact-links" aria-label="Contact and profiles"><a itemprop="email" href="mailto:javier.millanacosta@maastrichtuniversity.nl">javier.millanacosta@maastrichtuniversity.nl</a>
<a itemprop="sameAs" href="https://orcid.org/0000-0002-4166-7093">ORCID</a>
<a itemprop="sameAs" href="https://github.com/jmillanacosta">GitHub</a>
<a itemprop="sameAs" href="https://scholar.google.com/citations?user=e2py8rMAAAAJ&hl=nl">Google Scholar</a>
<a itemprop="sameAs" href="https://www.linkedin.com/in/javier-millanacosta/">LinkedIn</a>
<a itemprop="sameAs" href="https://pypi.org/user/jmillanacosta/">PyPI</a>
<a class="pdf-link" href="{{ "/output/pdf/javier-millan-acosta-cv.pdf" | relative_url }}" download>Download PDF</a>
</nav>

</header>

<section class="cv-section section-experience" aria-labelledby="experience" markdown="1">
<h2 id="experience"><a class="section-title" href="#experience">Experience</a></h2>
<div class="section-body" markdown="1">
<article class="cv-entry" markdown="1">
<header class="entry-heading">
<div><h3>Doctoral Researcher</h3><p class="entry-organization">Maastricht University, Department of Translational Genomics (TGX) · Maastricht, Netherlands</p></div>
<p class="entry-date">Jul 2022 – present</p>
</header>

Develop Python libraries, data pipelines, and web applications for biomedical knowledge graphs. Work includes RDF schema extraction, typed query interfaces, identifier reconciliation, and integration with graph databases.

Build a registry of life sciences RDF resources, relating vocabulary definitions, observed graph structures, and links between datasets to establish how they can be queried and combined. Study identifier deprecation and its effects on data integration over time.

</article>

<article class="cv-entry" markdown="1">
<header class="entry-heading">
<div><h3>Researcher (internship)</h3><p class="entry-organization">FrieslandCampina · Wageningen, Netherlands</p></div>
<p class="entry-date">Jun 2021 – Oct 2021</p>
</header>

Wrote scripts to extract, harmonize, and analyze food-systems and food-composition data; built dashboards with R Shiny and Power BI.

</article>

<article class="cv-entry" markdown="1">
<header class="entry-heading">
<div><h3>Researcher (internship)</h3><p class="entry-organization">Institut de Recerca Biomèdica de Lleida (IRBLleida), Clinical Neurosciences lab · Lleida, Spain</p></div>
<p class="entry-date">Jun 2018 – Sep 2018</p>
</header>

Investigated conditioning in ischemic-stroke recovery using ELISA, RNA extraction, and preparation of mouse blood and brain-slice samples.

</article>

</div>
</section>


<section class="cv-section section-publications" aria-labelledby="publications" markdown="1">
<h2 id="publications"><a class="section-title" href="#publications">Publications</a></h2>
<div class="section-body" markdown="1">
Further research outputs are available on [ORCID](https://orcid.org/0000-0002-4166-7093) and [Google Scholar](https://scholar.google.com/citations?user=e2py8rMAAAAJ&hl=nl).

<ul class="publication-list">
{% for pub in site.data.publications %}
  <li itemscope itemtype="https://schema.org/ScholarlyArticle">
    <span class="publication-title" itemprop="name">{{ pub.title }}</span>
    <span class="publication-details">{% if pub.journal %}<em itemprop="isPartOf">{{ pub.journal }}</em> · {% endif %}{{ pub.year }} · <span class="publication-type">{{ pub.type | replace: "-", " " | capitalize }}</span>{% if pub.url %} · <a itemprop="url" href="{{ pub.url }}">{{ pub.doi }}</a>{% endif %}</span>
  </li>
{% endfor %}
</ul>

</div>
</section>

<section class="cv-section section-open-source-software" aria-labelledby="open-source-software" markdown="1">
<h2 id="open-source-software"><a class="section-title" href="#open-source-software">Open-source software</a></h2>
<div class="section-body" markdown="1">
### Libraries and tools

<ul class="software-list">
{% for item in site.data.software_highlights %}
{% assign pkg = site.data.software | where: "name", item.name | first %}
  <li><strong><a href="{{ item.repository }}">{{ item.name }}</a></strong> - {{ item.summary }}{% if pkg.docs %} · <a href="{{ pkg.docs }}">docs</a>{% endif %}{% if pkg.pypi_url %} · <a href="{{ pkg.pypi_url }}">PyPI</a>{% endif %}</li>
{% endfor %}
</ul>

### Contributions to shared projects

- **[BioDataFuse](https://github.com/BioDataFuse/pyBiodatafuse)** - implemented RDF graph generation, the BDFGraph abstraction, AOP-Wiki and WikiPathways integrations, and GraphDB support; developed the corresponding API and Vue interface in [biodatafuseUI](https://github.com/BioDataFuse/biodatafuseUI).
- **[VHP4Safety Virtual Human Platform](https://platform.vhp4safety.nl)** - implemented services and frontend, search and filtering, and glossary integration across Flask and JavaScript. Also developed the **[AOP-Suite](https://aopsuite.cloud.vhp4safety.nl)**, for SPARQL-backed network construction, gene and compound queries, and interactive Cytoscape.js views, integrating the pyAOP library.
- **[eNanoMapper](https://github.com/enanomapper/ontologies)** - developed ontology terms and imports for nanosafety; added integrity tests, corrected OWL profile violations, and maintained build and validation workflows.
- **[BridgeDb](https://github.com/bridgedb/docker)** - automated Docker builds and versioned image releases across the webservice and data repositories; added webservice CI tests.
- **[CNV Pathway Atlas](https://cnvpathwayatlas.github.io/cnv-website/phenotype-browser/)** - built a phenotype-based browser with combined filters, ontology lookups, and an automated data-generation step.

**Personal tooling:** [seejobs](https://github.com/jmillanacosta/seejobs), a terminal interface for Slurm job monitoring, logs, and batch-script preparation over SSH.

[Merged contributions](https://github.com/search?q=is%3Apr+author%3Ajmillanacosta+is%3Amerged+-user%3Ajmillanacosta&type=pullrequests) · [All repositories](https://github.com/jmillanacosta?tab=repositories)

</div>
</section>

<section class="cv-section section-skills" aria-labelledby="skills" markdown="1">
<h2 id="skills"><a class="section-title" href="#skills">Technical skills</a></h2>
<div class="section-body" markdown="1">

- **Python engineering:** Library and CLI design, typed APIs and Pydantic models, package publishing, and Sphinx documentation.
- **Knowledge graphs:** RDF/OWL modelling, SPARQL, schema extraction and conversion with VoID, SHACL and LinkML; RDFLib and GraphDB; setting up SPARQL endpoints.
- **Data integration:** Source-specific parsers, identifier and label reconciliation, SSSOM mappings, release-history tracking, and provenance; pandas and Polars.
- **Web applications:** Flask and FastAPI services; Vue, JavaScript and TypeScript interfaces; Cytoscape.js network visualisation.
- **Testing and delivery:** pytest, tox, Ruff and mypy; GitHub Actions / Jenkins / GitLab for tests, data updates and releases; Docker and Slurm workflows.

</div>
</section>



<section class="cv-section section-research-communities-hackathons" aria-labelledby="research-communities-hackathons" markdown="1">
<h2 id="research-communities-hackathons"><a class="section-title" href="#research-communities-hackathons">Research communities & hackathons</a></h2>
<div class="section-body" markdown="1">

Research collaborations include [WikiPathways](https://www.wikipathways.org/team.html), eNanoMapper, SbD4Nano, and VHP4Safety.

- **ELIXIR BioHackathon Europe 2025 (Berlin, Germany)** - co-led [shape-driven knowledge graph integration](https://github.com/elixir-europe/biohackathon-projects-2025/blob/main/1.md), developing rdfsolve.
- **ELIXIR BioHackathon Europe 2024 (Barcelona, Spain)** - first author of the report on graph-schema discovery and transformation for database integration.
- **DBCLS BioHackathon 2025 (Mie, Japan)** - developed RDF schema tooling in rdfsolve.

</div>
</section>

<section class="cv-section section-teaching" aria-labelledby="teaching" markdown="1">
<h2 id="teaching"><a class="section-title" href="#teaching">Teaching</a></h2>
<div class="section-body" markdown="1">

Teaching contributions at Maastricht University:

<article class="cv-entry teaching-entry" itemscope itemtype="https://schema.org/Course" markdown="1">
<h3 itemprop="name"><a itemprop="url" href="https://www.maastrichtuniversity.nl/sites/default/files/2023-05/msp-course-catalogue-2022-2023.pdf#page=119">Programming in the Life Sciences</a></h3>
<p class="teaching-meta">Maastricht Science Programme · <span itemprop="courseCode">PRA3006</span></p>

Programming with life sciences: use web services connecting biological data and visualize results with JavaScript.

</article>

<article class="cv-entry teaching-entry" itemscope itemtype="https://schema.org/Course" markdown="1">
<h3 itemprop="name"><a itemprop="url" href="https://www.maastrichtuniversity.nl/sites/default/files/2026-05/fpn-course-catalog-2025-2026_4502_Ba_Brain_Science.pdf#page=8">Programming I</a></h3>
<p class="teaching-meta">BSc Brain Science · <span itemprop="courseCode">BRAIN1005</span></p>

Python fundamentals, data visualisation and computational models, linking programming exercises to mathematics and neuroscience.

</article>

<article class="cv-entry teaching-entry" itemscope itemtype="https://schema.org/Course" markdown="1">
<h3 itemprop="name"><a itemprop="url" href="https://fhmlweb.unimaas.nl/modules/Module.aspx?CODE=MBS1001&amp;PER=100&amp;YR=2025">Biomedical Challenges</a></h3>
<p class="teaching-meta">MSc Biomedical Sciences · <span itemprop="courseCode">MBS1001</span></p>

Supervised student groups during the execution of a research plan around Biomedical Challenges.

</article>

<article class="cv-entry teaching-entry" markdown="1">

### Bioinformatics tutorials

<p class="teaching-meta"><a href="https://www.maastrichtuniversity.nl/education/bachelor/programmes/biomedical-sciences/courses-and-curriculum">BSc Biomedical Sciences</a> · BBS</p>

Practical tutorials on BLAST sequence searches, biostatistics with R and SPSS, and biological databases for biomedical science students.

</article>

<article class="cv-entry teaching-entry" markdown="1">

### Problem-based learning (PBL) tutoring

Facilitate small-group learning, supporting students as they explore problems, formulate learning questions and discuss findings from independent study. [UM’s PBL approach](https://www.maastrichtuniversity.nl/pbl).

</article>

</div>
</section>

<section class="cv-section section-education" aria-labelledby="education" markdown="1">
<h2 id="education"><a class="section-title" href="#education">Education</a></h2>
<div class="section-body" markdown="1">
<article class="cv-entry" markdown="1">
<header class="entry-heading">
<div><h3>MSc Bioinformatics</h3><p class="entry-organization">Wageningen University &amp; Research</p></div>
<p class="entry-date">Sep 2019 – Nov 2021</p>
</header>

Thesis: proposed genes in de novo sesquiterpene synthesis in *Pieris brassicae*.

</article>

<article class="cv-entry" markdown="1">
<header class="entry-heading">
<div><h3>BSc Biotechnology</h3><p class="entry-organization">Universitat de Lleida</p></div>
<p class="entry-date">2015 – 2019</p>
</header>

Thesis: a review of the genetic mechanisms of animal migration. Included an Erasmus exchange at Wageningen University & Research.

</article>

</div>
</section>

<section class="cv-section section-languages-certifications" aria-labelledby="languages-certifications" markdown="1">
<h2 id="languages-certifications"><a class="section-title" href="#languages-certifications">Languages & certifications</a></h2>
<div class="section-body" markdown="1">
<span itemprop="knowsLanguage">Spanish</span> (native) · <span itemprop="knowsLanguage">Catalan</span> (native) · <span itemprop="knowsLanguage">English</span> (professional) · <span itemprop="knowsLanguage">Dutch</span> (B1-minus, certified by Maastricht University, Oct 2023)

### Teaching certifications

- <span itemprop="hasCredential" itemscope itemtype="https://schema.org/EducationalOccupationalCredential"><span itemprop="name">Introductory Course on the Principles of Problem-Based Learning (PBL)</span> - Maastricht University, 2023.</span>
- <span itemprop="hasCredential" itemscope itemtype="https://schema.org/EducationalOccupationalCredential"><span itemprop="name">Small Group Teacher Training (tutoring and coaching)</span> - Maastricht University, 2023.</span>
</div>
</section>










</div>

<script type="application/ld+json">
{
  "@context": {
    "@vocab": "https://schema.org/",
    "foaf": "http://xmlns.com/foaf/0.1/"
  },
  "@type": ["Person", "foaf:Person"],
  "name": "Javier Millán Acosta",
  "givenName": "Javier",
  "familyName": "Millán Acosta",
  "jobTitle": "Doctoral Researcher",
  "url": "https://jmillanacosta.github.io",
  "email": "mailto:javier.millanacosta@maastrichtuniversity.nl",
  "foaf:mbox": { "@id": "mailto:javier.millanacosta@maastrichtuniversity.nl" },
  "identifier": {
    "@type": "PropertyValue",
    "propertyID": "ORCID",
    "value": "https://orcid.org/0000-0002-4166-7093"
  },
  "sameAs": [
    "https://orcid.org/0000-0002-4166-7093",
    "https://github.com/jmillanacosta",
    "https://scholar.google.com/citations?user=e2py8rMAAAAJ&hl=nl",
    "https://www.linkedin.com/in/javier-millanacosta/",
    "https://pypi.org/user/jmillanacosta/",
    "https://www.wikidata.org/wiki/Q116446553",
    "https://www.wikipathways.org/authors/Jmillanacosta.html"
  ],
  "worksFor": {
    "@type": "Organization",
    "name": "Department of Translational Genomics",
    "parentOrganization": {
      "@type": "CollegeOrUniversity",
      "name": "Maastricht University",
      "sameAs": "https://ror.org/02jz4aj89"
    }
  },
  "alumniOf": [
    { "@type": "CollegeOrUniversity", "name": "Wageningen University & Research" },
    { "@type": "CollegeOrUniversity", "name": "Universitat de Lleida" }
  ],
  "knowsAbout": [
    "RDF",
    "SPARQL",
    "OWL",
    "SHACL",
    "SSSOM",
    "LinkML",
    "Knowledge graphs",
    "Ontology engineering",
    "Python",
    "Toxicology data",
    "FAIR data",
    "Backend",
    "Frontend",
    "DevOps"
  ]
}
</script>
