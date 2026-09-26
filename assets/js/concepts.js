// Concept links are resolved from the generated index.
(() => {
  const script = document.currentScript?.src || location.href;
  const indexUrl = new URL("../../concepts/index.json", script);

  const link = (element, concept) => {
    if (!concept || concept.path === location.pathname) return;
    const anchor = element.closest("a");
    if (anchor) {
      anchor.dataset.external = anchor.href;
      anchor.href = concept.path;
    } else {
      const anchor = document.createElement("a");
      anchor.className = "concept-link";
      anchor.href = concept.path;
      anchor.append(...element.childNodes);
      element.append(anchor);
    }
  };

  fetch(indexUrl)
    .then((response) => (response.ok ? response.json() : null))
    .then((index) => {
      if (!index) return;
      for (const element of document.querySelectorAll(
        "[data-concept], [data-kind]",
      )) {
        if (element.closest("[hidden]") || !element.textContent.trim())
          continue;
        const key = element.dataset.concept || `kind:${element.dataset.kind}`;
        link(element, index[key]);
      }
    })
    .catch(() => {});
})();
