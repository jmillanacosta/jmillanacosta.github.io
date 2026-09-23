// Section tracking and sidebar choreography. Every link works without this script.
(() => {
  const scriptUrl = document.currentScript?.src || location.href;
  const sidebar = document.querySelector(".sidebar");
  const nav = sidebar?.querySelector(".section-nav");
  if (!sidebar || !nav) return;
  const list = nav.querySelector("ol");
  const links = [...nav.querySelectorAll('ol a[href^="#"]')];
  const headings = links.map((link) =>
    document.getElementById(link.hash.slice(1)),
  );
  const sections = headings.map((heading) => heading?.closest("section"));
  const header = document.querySelector(".cv-header");
  const root = document.documentElement;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let current = -1;
  let chosen = -1;
  let queued = false;

  // The reading line sits a third of the way down, then slides to the bottom edge over the
  // last stretch of the page, so short closing sections each get their turn in order.
  const readingIndex = () => {
    const toEnd = root.scrollHeight - innerHeight - scrollY;
    const line = Math.max(innerHeight / 3, innerHeight - toEnd);
    let index = -1;
    sections.forEach((section, i) => {
      if (section && section.getBoundingClientRect().top <= line) index = i;
    });
    return index;
  };

  const highlight = (index) => {
    if (index === current) return;
    current = index;
    links.forEach((link, i) => {
      if (i === index) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
    // In the horizontal bar, center the current label without moving the page.
    const active = links[index];
    if (active && list && list.scrollWidth > list.clientWidth) {
      list.scrollTo({
        left: active.offsetLeft - (list.clientWidth - active.offsetWidth) / 2,
        behavior: reducedMotion.matches ? "auto" : "smooth",
      });
    }
  };

  const update = () => {
    queued = false;
    sidebar.toggleAttribute("data-at-top", scrollY < 1);
    // The sidebar profile repeats the header, so it appears only once the header has passed the rail's top edge.
    sidebar.toggleAttribute(
      "data-header-visible",
      !!header &&
        header.getBoundingClientRect().bottom >
          sidebar.getBoundingClientRect().top,
    );
    highlight(chosen >= 0 ? chosen : readingIndex());
  };

  const schedule = () => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(update);
    }
  };

  // A section the reader picked stays highlighted, even one too short to reach the reading
  // line, until they scroll by hand again.
  const choose = (hash) => {
    const index = links.findIndex((link) => link.hash === hash);
    if (index < 0) return release();
    chosen = index;
    highlight(index);
  };
  const release = () => {
    if (chosen < 0) return;
    chosen = -1;
    schedule();
  };
  addEventListener("click", (event) => {
    const link =
      event.target instanceof Element && event.target.closest('a[href^="#"]');
    if (link instanceof HTMLAnchorElement) choose(link.hash);
  });
  for (const type of ["wheel", "touchstart", "keydown"])
    addEventListener(type, release, { passive: true });

  // Scroll-driven motion on wide screens: the heading flies into the sidebar, and the section
  // list rides up beside Experience until it docks. CSS animates; this only measures.
  const title = header?.querySelector("h1 > span");
  const railName = sidebar.querySelector(".sidebar-name");
  const track = sidebar.querySelector(".sidebar-track");
  const firstLink = links[0];
  const firstHeading = headings[0];
  const motionMedia = matchMedia(
    "(width >= 75rem) and (prefers-reduced-motion: no-preference)",
  );
  const canAnimate =
    !!title &&
    !!railName &&
    !!track &&
    !!firstLink &&
    !!firstHeading &&
    CSS.supports("animation-timeline: scroll()");

  const measure = () => {
    if (!canAnimate || !motionMedia.matches) {
      delete root.dataset.flight;
      return;
    }
    // Loaded only here, so browsers without scroll timelines never parse these rules.
    if (!document.getElementById("cv-flight-styles")) {
      const styles = document.createElement("link");
      styles.id = "cv-flight-styles";
      styles.rel = "stylesheet";
      styles.href = new URL("../css/cv-flight.css", scriptUrl).href;
      styles.addEventListener("load", measure);
      document.head.append(styles);
    }
    root.dataset.flight = "";
    const heading = title.parentElement;
    const animated = [heading, title, track];
    for (const element of animated) element.style.animation = "none";
    const from = title.getBoundingClientRect();
    const to = railName.getBoundingClientRect();
    const headerBottom = header.getBoundingClientRect().bottom + scrollY;
    const link = firstLink.getBoundingClientRect();
    const target = firstHeading.getBoundingClientRect();
    for (const element of animated) element.style.animation = "";
    const scale =
      parseFloat(getComputedStyle(railName).fontSize) /
      parseFloat(getComputedStyle(title).fontSize);
    const set = (name, value) => root.style.setProperty(name, value);
    set("--fly-x", `${to.left - from.left}px`);
    set("--fly-y", `${to.top - (from.top + scrollY)}px`);
    set("--fly-scale", `${scale}`);
    // The flight ends as the header's bottom edge reaches the sidebar's top edge.
    set("--fly-range", `${Math.max(1, headerBottom - to.top)}px`);
    // At rest, the first section label lines up with the first section heading.
    const rest =
      target.top + target.height / 2 + scrollY - (link.top + link.height / 2);
    set("--rail-rest", `${Math.max(0, rest)}px`);
  };

  addEventListener("scroll", schedule, { passive: true });
  addEventListener("resize", () => {
    measure();
    schedule();
  });
  motionMedia.addEventListener("change", measure);
  measure();
  document.fonts?.ready.then(() => {
    measure();
    schedule();
  });
  if (location.hash) choose(location.hash);
  update();
})();
