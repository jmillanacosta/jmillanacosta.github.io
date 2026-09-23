// Renders the 1200×630 social preview (assets/og-image.png) with the site's fonts and palette.
// Rerun after changing the name, role or focus below: node scripts/build-og.mjs
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const OUTPUT = "assets/og-image.png";
const card = {
  name: "Javier Millán Acosta",
  role: "Doctoral Researcher · Maastricht University",
  focus:
    "Research software for biomedical knowledge graphs, RDF tooling, and data integration",
  url: "jmillanacosta.github.io",
};

/** @param {string} file */
const font = async (file) =>
  `url(data:font/woff2;base64,${(await readFile(`assets/fonts/${file}`)).toString("base64")}) format("woff2")`;

const html = `<!doctype html>
<html lang="en">
<style>
  @font-face { font-family: "Source Serif 4"; font-weight: 400; src: ${await font("source-serif-4-latin-400-normal.woff2")}; }
  @font-face { font-family: "Source Sans 3"; font-weight: 400; src: ${await font("source-sans-3-latin-400-normal.woff2")}; }
  @font-face { font-family: "Source Sans 3"; font-weight: 600; src: ${await font("source-sans-3-latin-600-normal.woff2")}; }
  * { box-sizing: border-box; margin: 0; }
  body {
    width: 1200px; height: 630px; padding: 96px 104px 84px;
    display: flex; flex-direction: column;
    background: #f8f6f1; color: #292b28; font-family: "Source Sans 3", sans-serif;
  }
  .rule { width: 100%; height: 6px; background: #355a4d; margin-bottom: 52px; }
  h1 { font: 400 88px/1.05 "Source Serif 4", serif; letter-spacing: -0.025em; }
  .role { margin-top: 26px; font-size: 34px; color: #565c55; }
  .focus { margin-top: 30px; max-width: 900px; font-size: 32px; line-height: 1.35; }
  .url { margin-top: auto; font-size: 26px; font-weight: 600; letter-spacing: 0.02em; color: #355a4d; }
</style>
<body>
  <div class="rule"></div>
  <h1>${card.name}</h1>
  <p class="role">${card.role}</p>
  <p class="focus">${card.focus}</p>
  <p class="url">${card.url}</p>
</body>
</html>`;

const browser = await chromium.launch({
  ...(process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH }
    : {}),
});
try {
  const page = await browser.newPage({
    viewport: { width: 1200, height: 630 },
  });
  await page.setContent(html);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: OUTPUT, type: "png" });
  console.log(`Generated ${OUTPUT}`);
} finally {
  await browser.close();
}
