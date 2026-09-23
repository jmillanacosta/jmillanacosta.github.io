// Renders the favicon (favicon.ico with 16 and 32 px images) and apple-touch-icon.png from the
// site's serif and palette. Rerun after changing the mark: node scripts/build-icons.mjs
import { readFile, writeFile } from "node:fs/promises";
import { chromium } from "playwright";

const font = `url(data:font/woff2;base64,${(await readFile("assets/fonts/source-serif-4-latin-400-normal.woff2")).toString("base64")}) format("woff2")`;

/** @param {number} size @param {number} radius */
const html = (size, radius) => `<!doctype html>
<style>
  @font-face { font-family: "Source Serif 4"; src: ${font}; }
  html, body { margin: 0; background: transparent; }
  div {
    width: ${size}px; height: ${size}px; border-radius: ${radius}px;
    display: grid; place-items: center;
    background: #355a4d; color: #f8f6f1;
    font: 400 ${Math.round(size * 0.78)}px/1 "Source Serif 4", serif;
    padding-bottom: ${Math.round(size * 0.06)}px; box-sizing: border-box;
  }
</style>
<div>J</div>`;

// An ICO file can hold PNG images directly: a 6-byte header, a 16-byte entry per image, then the data.
/** @param {Buffer[]} pngs @param {number[]} sizes */
function ico(pngs, sizes) {
  const header = Buffer.alloc(6 + 16 * pngs.length);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(pngs.length, 4);
  let offset = header.length;
  pngs.forEach((png, i) => {
    const entry = 6 + 16 * i;
    header.writeUInt8(sizes[i] % 256, entry);
    header.writeUInt8(sizes[i] % 256, entry + 1);
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(png.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...pngs]);
}

const browser = await chromium.launch({
  ...(process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH }
    : {}),
});
try {
  /** @param {number} size @param {number} radius */
  const render = async (size, radius) => {
    // Very small viewports stall screenshots, so render in a larger one and clip to the icon.
    const page = await browser.newPage({
      viewport: { width: 200, height: 200 },
    });
    await page.setContent(html(size, radius));
    await page.evaluate(() => document.fonts.ready);
    const png = await page.screenshot({
      omitBackground: true,
      clip: { x: 0, y: 0, width: size, height: size },
    });
    await page.close();
    return png;
  };
  const sizes = [16, 32];
  const pngs = [];
  for (const size of sizes) pngs.push(await render(size, size / 8));
  await writeFile("favicon.ico", ico(pngs, sizes));
  // iOS applies its own rounded mask, so the touch icon is a full square.
  await writeFile("apple-touch-icon.png", await render(180, 0));
  console.log("Generated favicon.ico and apple-touch-icon.png");
} finally {
  await browser.close();
}
