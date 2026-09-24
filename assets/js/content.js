// The content page's filter: narrows the table to rows whose name or kind contains every word
// typed, hides groups left empty, and says how many rows show. Without it, the table is complete.
(() => {
  const toolbar = document.querySelector(".content-toolbar");
  const input = toolbar?.querySelector(".content-filter");
  const count = toolbar?.querySelector(".content-count");
  if (!toolbar || !input || !count) return;
  const groups = [...document.querySelectorAll(".content-group")];
  const rowsOf = (group) => [
    ...group.querySelectorAll("tr:not(.content-group-head)"),
  ];
  const rows = groups.flatMap((group) => [
    ...group.querySelectorAll("tr:not(.content-group-head)"),
  ]);
  const text = new Map(rows.map((row) => [row, row.textContent.toLowerCase()]));
  const template = count.dataset.template;

  const apply = () => {
    const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const row of rows) {
      const match = words.every((word) => text.get(row).includes(word));
      row.hidden = !match;
      if (match) shown += 1;
    }
    for (const group of groups) {
      group.hidden = ![...group.rows].some(
        (row) => !row.hidden && !row.classList.contains("content-group-head"),
      );
    }
    // Group counts and the total follow the filter; the site's own pages are not counted.
    let total = 0;
    shown = 0;
    for (const group of groups) {
      const visible = rowsOf(group).filter((row) => !row.hidden).length;
      const label = group.querySelector(".content-group-count");
      if (label) label.textContent = visible;
      if (!group.classList.contains("content-pages")) {
        total += rowsOf(group).length;
        shown += visible;
      }
    }
    count.textContent = template
      .replace("{shown}", shown)
      .replace("{total}", total);
  };

  input.addEventListener("input", apply);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      input.value = "";
      apply();
    }
  });
  toolbar.hidden = false;
  apply();
})();
