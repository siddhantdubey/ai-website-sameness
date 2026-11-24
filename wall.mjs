// Screenshot a local HTML contact sheet to PNG with the same headless Chromium as measure.mjs.
//   node wall.mjs <sheet.html> <out.png> <width> <height>
// One browser page at a time. Images in the sheet are pre-scaled, so the page is drawn 1:1.
import { chromium } from 'playwright-core';
import { pathToFileURL } from 'node:url';

const LIBS = process.env.AISM_LD_LIBRARY_PATH || '/home/research/.cache/sheepdog-browser/root/usr/lib/x86_64-linux-gnu';
const FONTCONFIG = process.env.AISM_FONTCONFIG_FILE || '/data/scratch/ai-sameness/browser-fonts/fonts.conf';

const [html, out, w, h] = process.argv.slice(2);
if (!html || !out || !w || !h) {
  console.error('usage: node wall.mjs <sheet.html> <out.png> <width> <height>');
  process.exit(2);
}
const browser = await chromium.launch({
  args: ['--disable-gpu', '--font-render-hinting=none'],
  env: { ...process.env, LD_LIBRARY_PATH: `${LIBS}:${process.env.LD_LIBRARY_PATH || ''}`, FONTCONFIG_FILE: FONTCONFIG },
  timeout: 300000,
});
try {
  const page = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(html).href, { waitUntil: 'load', timeout: 120000 });
  await page.waitForFunction(() => [...document.images].every((i) => i.complete), null, { timeout: 120000 });
  const broken = await page.evaluate(() => [...document.images].filter((i) => !i.naturalWidth).map((i) => i.getAttribute('src')));
  if (broken.length) console.error(`broken images: ${broken.join(', ')}`);
  const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
  if (size[0] > +w || size[1] > +h) console.error(`sheet overflows viewport: ${size} > ${w}x${h}`);
  await page.screenshot({ path: out, fullPage: false, clip: { x: 0, y: 0, width: +w, height: +h } });
  console.log(`wrote ${out} ${w}x${h} images=${await page.evaluate(() => document.images.length)} broken=${broken.length}`);
} finally {
  await browser.close();
}
