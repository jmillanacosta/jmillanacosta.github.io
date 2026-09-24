// Names of concepts (organizations, places, software, events, publications, people, skills)
(() => {
  const scriptUrl = document.currentScript?.src || location.href;
  const indexUrl = new URL("../../concepts/index.json", scriptUrl);

  const nameOf = (element) =>
    (element.getAttribute("content") ?? element.textContent).trim();

  const keyOf = (node) => {
    const iri = node.getAttribute("about") || node.getAttribute("resource");
    if (iri) return iri;
    const name = [...node.querySelectorAll('[property~="name"]')].find(
      (element) => element.closest("[about], [typeof]") === node,
    );
    const types = (node.getAttribute("typeof") || "").split(/\s+/).sort();
    return name && types[0] ? `b:${types[0]}|${nameOf(name)}` : null;
  };

  const link = (index) => {
    for (const name of document.querySelectorAll('[property~="name"]')) {
      if (!name.textContent.trim() || name.closest("[hidden], .concept"))
        continue;
      const node = name.hasAttribute("about")
        ? name
        : name.closest("[about], [typeof]");
      const concept = node && index[keyOf(node)];
      if (!concept || concept.path === location.pathname) continue;
      const anchor = name.closest("a");
      if (anchor && node.contains(anchor)) {
        // The name already links out (a repository, a project site); it now opens the concept
        // page, which links out in turn.
        anchor.dataset.external = anchor.href;
        anchor.href = concept.path;
      } else if (!anchor) {
        const inner = document.createElement("a");
        inner.className = "concept-link";
        inner.href = concept.path;
        inner.append(...name.childNodes);
        name.append(inner);
      }
    }
  };

  // Kinds (Hackathon, Journal article) are text values: a genre or keywords statement, or an
  // element naming one (data-kind="keywords|hackathon"), leads to the kind's page.
  const linkKinds = (index) => {
    const kinds = document.querySelectorAll(
      '[data-kind], [property~="genre"], [property~="keywords"]',
    );
    for (const element of kinds) {
      if (!element.textContent.trim() || element.closest("a, [hidden]"))
        continue;
      const property = element.dataset.kind
        ? null
        : element
            .getAttribute("property")
            .split(/\s+/)
            .find((p) => p === "genre" || p === "keywords");
      const key = element.dataset.kind || `${property}|${nameOf(element)}`;
      const concept = index[`kind:${key}`];
      if (!concept || concept.path === location.pathname) continue;
      const inner = document.createElement("a");
      inner.className = "concept-link";
      inner.href = concept.path;
      inner.append(...element.childNodes);
      element.append(inner);
    }
  };

  fetch(indexUrl)
    .then((response) => (response.ok ? response.json() : null))
    .then((index) => {
      if (!index) return;
      link(index);
      linkKinds(index);
    })
    .catch(() => {});
})();
