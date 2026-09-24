// Render local HTML with the Chromium build pinned by package-lock.json.
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

/**
 * @template T
 * @param {Promise<T>} operation
 * @param {string} label
 * @returns {Promise<T>}
 */
function bounded(operation, label, timeout = 60000) {
  /** @type {NodeJS.Timeout | undefined} */
  let timer;
  return Promise.race([
    operation,
    new Promise((_, reject) => {
      timer = setTimeout(
        () =>
          reject(
            new Error(
              `PDF renderer timed out after ${timeout / 1000}s: ${label}`,
            ),
          ),
        timeout,
      );
    }),
  ]).finally(() => clearTimeout(timer));
}

const SITE_URL = "https://jmillanacosta.github.io";
const FONT_DIR = fileURLToPath(new URL("../fonts/", import.meta.url));

// Chromium embeds web fonts in PDFs as unhinted Type 3 glyphs; only installed fonts are
// embedded as real TrueType. So the site's typefaces are installed for this browser alone,
// through a private fontconfig setup (Linux, as in CI), and print uses them by family name.
async function privateFontconfig() {
  const dir = await mkdtemp(path.join(tmpdir(), "cv-fonts-"));
  const file = path.join(dir, "fonts.conf");
  await writeFile(
    file,
    `<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>${FONT_DIR}</dir>
  <cachedir>${dir}</cachedir>
</fontconfig>
`,
  );
  return { file, cleanup: () => rm(dir, { recursive: true, force: true }) };
}

// The CV page is rendered by default; the PDF and the layout check both use it.
export async function openCV(siteDir = "_site", page = "cv/index.html") {
  let html = await readFile(path.join(siteDir, page), "utf8");
  const css = await readFile(path.join(siteDir, "assets/css/cv.css"), "utf8");
  if (!html.includes("/assets/css/cv.css"))
    throw new Error("Build the CV first: bundle exec jekyll build");
  html = html.replace(
    /<link\s+rel="stylesheet"\s+href="[^"]*\/assets\/css\/cv\.css"\s*\/>/,
    // Without the web font declarations, the same families resolve to the installed copies.
    () => `<style>${css.replace(/@font-face\s*{[^}]*}/g, "")}</style>`,
  );
  html = html.replace("<head>", `<head><base href="${SITE_URL}/">`);
  // The printed CV links to the original sources, not to the site's concept pages.
  html = html.replace(/<script[^>]*concepts\.js[^>]*><\/script>/, "");

  const fonts = await privateFontconfig();
  const browser = await chromium
    .launch({
      headless: true,
      timeout: 60000,
      env: { ...process.env, FONTCONFIG_FILE: fonts.file },
      ...(process.env.CHROME_PATH
        ? { executablePath: process.env.CHROME_PATH }
        : {}),
    })
    .catch(async (error) => {
      await fonts.cleanup();
      throw error;
    });
  const close = () => browser.close().finally(fonts.cleanup);
  try {
    const context = await bounded(
      browser.newContext({
        viewport: { width: 1280, height: 1000 },
        javaScriptEnabled: false,
      }),
      "create context",
    );
    // The stylesheet is inlined and fonts are installed locally. Nothing is fetched remotely.
    await context.route("**/*", (route) => route.abort());
    const page = await bounded(context.newPage(), "create page");
    page.setDefaultTimeout(30000);
    await page.setContent(html, { waitUntil: "load", timeout: 30000 });
    await page.evaluate(() => document.fonts.ready);
    const session = await context.newCDPSession(page);
    /** @type {(method: any, params?: object) => Promise<any>} */
    const call = (method, params = {}) =>
      bounded(session.send(method, params), method);
    return { call, close };
  } catch (error) {
    // Keep the original failure and its browser diagnostics if cleanup also fails.
    await close().catch(() => {});
    throw error;
  }
}
