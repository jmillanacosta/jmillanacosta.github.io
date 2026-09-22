// Render local HTML with the Chromium build pinned by package-lock.json.
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

function bounded(operation, label, timeout = 60000) {
  let timer;
  return Promise.race([
    operation,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`PDF renderer timed out after ${timeout / 1000}s: ${label}`)), timeout);
    }),
  ]).finally(() => clearTimeout(timer));
}

export async function openCV(siteDir = '_site') {
  let html = await readFile(path.join(siteDir, 'index.html'), 'utf8');
  const css = await readFile(path.join(siteDir, 'assets/css/cv.css'), 'utf8');
  if (!html.includes('/assets/css/cv.css')) throw new Error('Build the CV first: bundle exec jekyll build');
  html = html.replace(/<link\s+rel="stylesheet"\s+href="[^"]*\/assets\/css\/cv\.css"\s*\/>/, () => `<style>${css}</style>`);
  html = html.replace('<head>', '<head><base href="https://jmillanacosta.github.io/">');

  const browser = await chromium.launch({
    headless: true,
    timeout: 60000,
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
  });
  const close = () => browser.close();
  try {
    const context = await bounded(browser.newContext({ viewport: { width: 1280, height: 1000 }, javaScriptEnabled: false }), 'create context');
    // The CV's stylesheet is inlined. No remote resources are needed for export.
    await context.route('**/*', route => route.abort());
    const page = await bounded(context.newPage(), 'create page');
    page.setDefaultTimeout(30000);
    await page.setContent(html, { waitUntil: 'load', timeout: 30000 });
    await page.evaluate(() => document.fonts.ready);
    const session = await context.newCDPSession(page);
    const call = (method, params = {}) => bounded(session.send(method, params), method);
    return { call, close };
  } catch (error) {
    // Keep the original failure and its browser diagnostics if cleanup also fails.
    await close().catch(() => {});
    throw error;
  }
}
