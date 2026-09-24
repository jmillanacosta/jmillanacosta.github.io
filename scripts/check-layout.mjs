// Checks actual rendered overflow, responsive layout, and the fixed light palette.
// The section bar's labels scroll sideways inside their own list, so they may extend past the edge.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { openCV } from "./lib/render-cv.mjs";
const renderer = await openCV();
const directory = process.env.CV_SCREENSHOT_DIR || "/tmp/cv-layout";
await mkdir(directory, { recursive: true });
try {
  for (const width of [320, 390, 600, 760, 900, 1280]) {
    await renderer.call("Emulation.setDeviceMetricsOverride", {
      width,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: width < 480,
    });
    await renderer.call("Emulation.setEmulatedMedia", {
      media: "screen",
      features: [{ name: "prefers-color-scheme", value: "dark" }],
    });
    const { result } = await renderer.call("Runtime.evaluate", {
      expression: `JSON.stringify({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,background:getComputedStyle(document.body).backgroundColor,sections:document.querySelectorAll('.cv-section').length,entries:document.querySelectorAll('.cv-entry').length,overflow:[...document.querySelectorAll('main *')].filter(e=>{const r=e.getBoundingClientRect();return r.width && !e.closest('.section-nav ol') && (r.right>innerWidth+1||r.left<0)}).map(e=>e.tagName+'.'+e.className)})`,
    });
    const layout = JSON.parse(result.value);
    assert.equal(layout.width, width);
    assert.ok(layout.scrollWidth <= width, "Horizontal page overflow");
    assert.deepEqual(
      layout.overflow,
      [],
      "An element extends outside the viewport",
    );
    assert.equal(layout.background, "rgb(253, 253, 252)");
    assert.equal(layout.sections, 8);
    assert.equal(layout.entries, 10);
    const { data } = await renderer.call("Page.captureScreenshot", {
      captureBeyondViewport: true,
    });
    await writeFile(
      `${directory}/cv-${width}.png`,
      Buffer.from(data, "base64"),
    );
    if ([390, 1280].includes(width)) {
      const view = await renderer.call("Page.captureScreenshot", {
        captureBeyondViewport: false,
      });
      await writeFile(
        `${directory}/cv-${width}-viewport.png`,
        Buffer.from(view.data, "base64"),
      );
    }
    console.log(
      `${width}px: no overflow; 8 sections, 10 entries; light theme under dark preference`,
    );
  }
} finally {
  await renderer.close();
}
