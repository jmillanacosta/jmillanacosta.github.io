// Run after `bundle exec jekyll build`. Uses the same HTML and print CSS as the site.
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { openCV } from './lib/render-cv.mjs';

const siteDir = process.argv[2] || '_site';
const relativeOutput = 'output/pdf/javier-millan-acosta-cv.pdf';
const renderer = await openCV(siteDir);
try {
  await renderer.call('Emulation.setEmulatedMedia', { media: 'print' });
  const { data } = await renderer.call('Page.printToPDF', {
    printBackground: true,
    preferCSSPageSize: true,
    generateTaggedPDF: true,
    generateDocumentOutline: true,
    displayHeaderFooter: true,
    headerTemplate: '<span></span>',
    footerTemplate: '<div style="font:8px Arial,sans-serif;color:#62665e;width:100%;margin:0 15mm;display:flex;justify-content:space-between"><span>Javier Millán Acosta · CV</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
  });
  const pdf = Buffer.from(data, 'base64');
  for (const destination of [relativeOutput, path.join(siteDir, relativeOutput)]) {
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, pdf);
  }
  console.log(`Generated ${relativeOutput} (${pdf.length} bytes), including the site download.`);
} finally { await renderer.close(); }
