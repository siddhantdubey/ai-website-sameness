#!/usr/bin/env node
// Measure single-file HTML landing pages in headless Chromium at 1440x900.
// Usage: node measure.mjs --pages <dir> --shots <dir> --out results.csv [--only id,id] [--jsonl path] [--concurrency 1|2]
// See README.md for the definition of every column.

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';
import { PNG } from 'pngjs';
import { measureInPage } from './lib/inpage.js';
import { hueBucket, hueFamily, hexToRgb, luminance, rgbToHex } from './lib/colour.mjs';
import { normaliseSection, detectFrameworks, FONT_CSS_HOSTS } from './lib/tables.mjs';

const TOOL_VERSION = '1.2.0';
const VIEWPORT = { width: 1440, height: 900 };
const LOAD_TIMEOUT_MS = 15000;
const IDLE_WAIT_MS = 4000;
const FONTS_WAIT_MS = 4000;
const PAGE_HARD_TIMEOUT_MS = 60000;

// Browser runtime: Playwright's chromium_headless_shell-1255 (matches playwright-core 1.65.1),
// shared libraries unpacked in the existing user-space kit, fonts from a project fontconfig
// (DejaVu + Liberation + Noto Color Emoji). Override with env vars if moved.
const LIBS = process.env.AISM_LD_LIBRARY_PATH || '/home/research/.cache/sheepdog-browser/root/usr/lib/x86_64-linux-gnu';
const FONTCONFIG = process.env.AISM_FONTCONFIG_FILE || '/data/scratch/ai-sameness/browser-fonts/fonts.conf';

function parseArgs(argv) {
  const a = { concurrency: 2 };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    const v = () => argv[++i];
    if (k === '--pages') a.pages = v();
    else if (k === '--shots') a.shots = v();
    else if (k === '--out') a.out = v();
    else if (k === '--jsonl') a.jsonl = v();
    else if (k === '--only') a.only = v().split(',').map((s) => s.trim()).filter(Boolean);
    else if (k === '--concurrency') a.concurrency = Math.max(1, Math.min(2, parseInt(v(), 10) || 1));
    else if (k === '-h' || k === '--help') a.help = true;
    else throw new Error(`unknown argument ${k}`);
  }
  if (a.help || !a.pages || !a.shots || !a.out) {
    console.log('usage: node measure.mjs --pages <dir> --shots <dir> --out results.csv [--only id,...] [--jsonl results.jsonl] [--concurrency 1|2]');
    process.exit(a.help ? 0 : 2);
  }
  if (!a.jsonl) a.jsonl = a.out.replace(/\.csv$/i, '') + '.jsonl';
  return a;
}

// ---------- CSV ----------
const COLUMNS = [
  'id', 'model', 'prompt', 'run', 'file_bytes', 'source_notes', 'color_scheme', 'compat_mode',
  'bg_color', 'bg_source', 'bg_coverage', 'bg_hue_bucket', 'bg_hue_family', 'bg_lightness', 'bg_luminance', 'theme',
  'accent_color', 'accent_source', 'accent_gradient', 'accent_hue_bucket', 'accent_hue_family',
  'gradient_present', 'gradient_where', 'gradient_fullpage', 'gradient_fullpage_where',
  'h1_count', 'h1_text', 'h1_in_viewport', 'h1_centered', 'h1_text_align',
  'h1_font_declared', 'h1_font_rendered', 'body_font_declared', 'body_font_rendered',
  'external_fonts', 'external_font_hosts',
  'buttons_in_viewport', 'buttons_in_viewport_nav', 'buttons_in_viewport_nonnav', 'primary_button_text',
  'three_card_grid', 'max_cards_in_row', 'rows_3up', 'rows_4up', 'grid_cards_total',
  'emoji_icon_count', 'emoji_total', 'svg_icon_count', 'svg_total', 'icon_font_count', 'img_icon_count',
  'section_order', 'section_order_norm', 'n_h2', 'n_sections',
  'frameworks', 'external_requests', 'external_hosts', 'has_external_images', 'external_image_hosts', 'failed_requests',
  'nav_present', 'hero_has_image', 'word_count',
  'load_error', 'console_error_count', 'page_error_count', 'measure_notes', 'viewport_shot', 'full_shot',
  // added in tool 1.1.0 (appended so existing column positions do not move)
  'arm', 'three_up_any', 'three_up_pricing', 'three_up_testimonials', 'three_up_steps', 'three_up_in_mockup', 'three_up_stats',
  'buttons_in_viewport_mockup', 'mockup_present',
  // added in tool 1.2.0
  'three_up_menu', 'three_up_team', 'three_up_gallery',
];
function csvCell(v) {
  if (v === null || v === undefined) return '';
  const s = Array.isArray(v) ? v.join(';') : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
const yn = (b) => (b === null || b === undefined ? 'na' : b ? 'y' : 'n');

// id = model__prompt__run[__arm]; arm defaults to 'default'.
function parseId(id) {
  const parts = id.split('__');
  if (parts.length === 3 || parts.length === 4) return { model: parts[0], prompt: parts[1], run: parts[2].replace(/^r/, ''), arm: parts[3] || 'default' };
  return { model: 'na', prompt: 'na', run: 'na', arm: 'na', bad: true };
}

// ---------- card-row and section classification (definitions in MEASURES.md) ----------
const kwLabel = (eyebrow, heading) => {
  const e = eyebrow ? normaliseSection(eyebrow) : 'other';
  return e !== 'other' ? e : normaliseSection(heading);
};
const STEP_TOKENS = ['step', 'steps', 'process', 'pipeline', 'timeline', 'workflow', 'onboarding'];
const PRICE_TOKENS = ['pricing', 'price', 'prices', 'plans', 'plan', 'tiers', 'tier'];
const QUOTE_TOKENS = ['testimonial', 'testimonials', 'quote', 'quotes', 'review', 'reviews'];
const MENU_TOKENS = ['menu', 'dish', 'dishes'];
const GALLERY_TOKENS = ['gallery', 'portfolio', 'photos', 'photo', 'plates', 'frames', 'archive'];
const TEAM_TOKENS = ['team', 'people', 'attorneys', 'lawyers', 'staff', 'founders', 'bios', 'bio', 'members', 'chefs'];
// Each card row gets exactly one kind, first match wins (v1.2.0):
//  in_mockup > menu > pricing > testimonials > team > stats > steps > gallery > feature
// Numbering ("01", "Step 2") no longer decides steps on its own: on the main run it marked
// numbered services / practice areas / feature cards as steps. Steps need a how-it-works
// section keyword or a step-like class token.
function classifyRow(r, blocks) {
  const sec = r.sectionIdx >= 0 && blocks[r.sectionIdx] ? blocks[r.sectionIdx] : null;
  const kw = sec ? kwLabel(sec.eyebrow, sec.text) : 'hero';
  const tok = (list) => r.classTokens.some((t) => list.includes(t));
  const why = [];
  let kind;
  if (r.mockup) { kind = 'in_mockup'; why.push('inside-mockup:' + r.mockup); }
  else if (kw === 'menu' || tok(MENU_TOKENS)) { kind = 'menu'; why.push(`section=${kw}`); }
  else if (r.priceCards >= 2 || kw === 'pricing' || tok(PRICE_TOKENS)) { kind = 'pricing'; why.push(`price-cards=${r.priceCards},section=${kw}`); }
  else if (r.quoteCards >= 2 || kw === 'testimonials' || tok(QUOTE_TOKENS)) { kind = 'testimonials'; why.push(`quote-cards=${r.quoteCards},section=${kw}`); }
  else if (kw === 'team' || tok(TEAM_TOKENS)) { kind = 'team'; why.push(`section=${kw}`); }
  else if (r.statCards >= 2) { kind = 'stats'; why.push(`stat-cards=${r.statCards}`); }
  else if (kw === 'how-it-works' || tok(STEP_TOKENS)) { kind = 'steps'; why.push(`section=${kw},numbered-cards=${r.numberedCards}`); }
  else if ((r.galleryCards || 0) >= 2 || tok(GALLERY_TOKENS)) { kind = 'gallery'; why.push(`gallery-cards=${r.galleryCards}`); }
  else { kind = 'feature'; why.push(`section=${kw}` + (r.numberedCards ? `,numbered-cards=${r.numberedCards}` : '')); }
  return { kind, why: why.join(','), sectionKeyword: kw };
}
// Section label: keyword on eyebrow, then on heading; if 'other', content rules in order:
//  >=3 questions or any <details> -> faq; >=2 price matches -> pricing; any quote -> testimonials;
//  a card row of kind steps -> how-it-works; a card row of kind feature -> features;
//  last heading with < 120 words and a button/input/form -> cta.
function sectionLabel(b, i, blocks, rowKinds) {
  const kw = kwLabel(b.eyebrow, b.text);
  if (kw !== 'other') return { label: kw, via: b.eyebrow && normaliseSection(b.eyebrow) !== 'other' ? 'eyebrow' : 'heading' };
  if (b.questions >= 3 || b.details > 0) return { label: 'faq', via: 'content:questions' };
  if (b.price >= 2) return { label: 'pricing', via: 'content:prices' };
  if (b.quotes > 0) return { label: 'testimonials', via: 'content:quotes' };
  const kinds = rowKinds.filter((x) => x.sectionIdx === i).map((x) => x.kind);
  if (kinds.includes('steps')) return { label: 'how-it-works', via: 'content:step-row' };
  if (kinds.includes('feature')) return { label: 'features', via: 'content:feature-row' };
  if (i === blocks.length - 1 && b.words < 120 && b.hasAction) return { label: 'cta', via: 'content:last-short-with-action' };
  return { label: 'other', via: 'none' };
}

// Dominant colour of a rectangle of a PNG: quantise to 4 bits/channel, take the
// most populated bin, return the mean of the pixels in that bin.
function dominantColour(pngBuf, rect) {
  const png = PNG.sync.read(pngBuf);
  const x0 = Math.max(0, Math.floor(rect.x)), y0 = Math.max(0, Math.floor(rect.y));
  const x1 = Math.min(png.width, Math.ceil(rect.x2)), y1 = Math.min(png.height, Math.ceil(rect.y2));
  const bins = new Map();
  const step = 2; // every 2nd pixel in each direction
  let n = 0;
  for (let y = y0; y < y1; y += step) for (let x = x0; x < x1; x += step) {
    const i = (y * png.width + x) * 4;
    const r = png.data[i], g = png.data[i + 1], b = png.data[i + 2];
    const k = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
    let e = bins.get(k); if (!e) { e = [0, 0, 0, 0]; bins.set(k, e); }
    e[0]++; e[1] += r; e[2] += g; e[3] += b; n++;
  }
  if (!n) return null;
  const [cnt, R, G, B] = [...bins.values()].sort((a, b) => b[0] - a[0])[0];
  return { hex: rgbToHex({ r: R / cnt, g: G / cnt, b: B / cnt }), share: cnt / n };
}

const withTimeout = (p, ms, label) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(`${label} timed out after ${ms}ms`)), ms))]);
// Platform font names of static instances / variable-font default instances carry a
// style suffix ("Source Sans 3 ExtraLight", "Playfair Display SemiBold"). Strip it so the
// column names the family. The raw name stays in results.jsonl (h1_fonts / body_fonts).
const STYLE_SUFFIX = /(\s+(Thin|Hairline|ExtraLight|Extra Light|UltraLight|Light|Book|Regular|Normal|Medium|SemiBold|Semi Bold|DemiBold|Bold|ExtraBold|Extra Bold|UltraBold|Black|Heavy|Italic|Oblique|Variable|VF))+$/i;
const familyOf = (name) => (name || '').replace(STYLE_SUFFIX, '').trim() || name;
const hostOf = (u) => { try { return new URL(u).host; } catch { return ''; } };

async function settle(page, notes) {
  // Scroll through the page so scroll-triggered reveals / lazy images fire, as a visitor's would.
  try {
    await withTimeout(page.evaluate(async () => {
      const H = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
      // behavior:'instant' so pages with `scroll-behavior: smooth` are not caught mid-scroll by the screenshot
      const go = (y) => window.scrollTo({ top: y, left: 0, behavior: 'instant' });
      for (let y = 0, i = 0; y < H() && i < 40; y += 700, i++) { go(y); await new Promise((r) => setTimeout(r, 120)); }
      go(H());
      await new Promise((r) => setTimeout(r, 250));
      go(0);
      for (let i = 0; i < 20 && window.scrollY !== 0; i++) { go(0); await new Promise((r) => setTimeout(r, 100)); }
      await new Promise((r) => setTimeout(r, 400));
    }), 20000, 'scroll pass');
  } catch (e) { notes.push('scroll-pass-failed:' + e.message.split('\n')[0]); }
  // Finish finite CSS/Web animations and transitions; cancel infinite ones (back to their un-animated state).
  try {
    const r = await withTimeout(page.evaluate(() => {
      let fin = 0, can = 0;
      for (const a of document.getAnimations()) {
        try {
          const t = a.effect && a.effect.getComputedTiming();
          if (t && t.endTime === Infinity) { a.cancel(); can++; } else { a.finish(); fin++; }
        } catch { /* ignore */ }
      }
      return { fin, can };
    }), 10000, 'animation settle');
    if (r.can) notes.push(`animations:${r.fin}-finished,${r.can}-infinite-cancelled`);
  } catch (e) { notes.push('animation-settle-failed:' + e.message.split('\n')[0]); }
  await page.waitForTimeout(300);
}

async function renderedFonts(cdp, selector) {
  const { root } = await cdp.send('DOM.getDocument', { depth: 0 });
  const { nodeIds } = await cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector });
  const agg = new Map();
  for (const nodeId of nodeIds.slice(0, 200)) {
    try {
      const { fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId });
      for (const f of fonts) {
        const e = agg.get(f.familyName) || { family: f.familyName, glyphs: 0, custom: f.isCustomFont };
        e.glyphs += f.glyphCount; agg.set(f.familyName, e);
      }
    } catch { /* node without layout */ }
  }
  return [...agg.values()].sort((a, b) => b.glyphs - a.glyphs);
}

function sourceNotesFor(buf) {
  const src = buf.toString('utf8');
  const sourceNotes = [];
  if (buf.length === 0) sourceNotes.push('empty-file');
  else if (!src.trim()) sourceNotes.push('whitespace-only');
  if (/^\s*```/.test(src) || /```\s*$/.test(src)) sourceNotes.push('markdown-fence-in-source');
  if (src.trim() && !/<html[\s>]/i.test(src)) sourceNotes.push('no-<html>-tag');
  if (src.trim() && !/<!doctype html/i.test(src)) sourceNotes.push('no-doctype');
  if (src.trim() && !/<\/html>\s*(```)?\s*$/i.test(src)) sourceNotes.push('no-closing-</html>-at-end(truncated?)');
  return sourceNotes;
}

async function measureOne(browser, file, id, shotsDir) {
  const notes = [];
  const rec = { id, tool_version: TOOL_VERSION, measured_at: new Date().toISOString(), file };
  const buf = fs.readFileSync(file);
  rec.file_bytes = buf.length;
  rec.source_notes = sourceNotesFor(buf);

  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, ignoreHTTPSErrors: false });
  const requests = [];
  const failed = [];
  const consoleErrors = [];
  const pageErrors = [];
  try {
    const page = await context.newPage();
    page.on('popup', (p) => { notes.push('popup-closed'); p.close().catch(() => {}); });
    page.on('request', (r) => requests.push({ url: r.url(), type: r.resourceType() }));
    page.on('requestfailed', (r) => failed.push({ url: r.url(), error: r.failure()?.errorText }));
    page.on('response', (r) => { if (r.status() >= 400) failed.push({ url: r.url(), error: 'http-' + r.status() }); });
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
    page.on('pageerror', (e) => pageErrors.push(String(e.message || e).slice(0, 300)));
    page.on('dialog', (d) => { notes.push('dialog:' + d.type()); d.dismiss().catch(() => {}); });

    const url = pathToFileURL(path.resolve(file)).href;
    try {
      await page.goto(url, { waitUntil: 'load', timeout: LOAD_TIMEOUT_MS });
    } catch (e) {
      rec.load_error = e.message.split('\n')[0];
      notes.push('load-timeout-or-error:proceeded-with-what-rendered');
    }
    try { await page.waitForLoadState('networkidle', { timeout: IDLE_WAIT_MS }); } catch { notes.push('network-not-idle-after-' + IDLE_WAIT_MS + 'ms'); }
    try {
      const ok = await withTimeout(page.evaluate(() => document.fonts.ready.then(() => true)), FONTS_WAIT_MS, 'fonts.ready');
      if (!ok) notes.push('fonts-not-ready');
    } catch { notes.push('fonts-not-ready-after-' + FONTS_WAIT_MS + 'ms'); }
    if (page.url() !== url) notes.push('page-navigated-away:' + page.url().slice(0, 120));
    await settle(page, notes);

    let m = null;
    try {
      m = await withTimeout(page.evaluate(measureInPage, {}), 30000, 'in-page measure');
    } catch (e) { notes.push('measure-failed:' + e.message.split('\n')[0]); }
    rec.raw = m;

    // Rendered fonts via CDP (glyph counts per platform font actually used).
    if (m) {
      try {
        await withTimeout((async () => {
          const cdp = await context.newCDPSession(page);
          await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
          if (m.h1 && m.h1.text !== undefined) rec.h1_fonts = await renderedFonts(cdp, '[data-aism-h1], [data-aism-h1] *');
          if (m.bodyFont) rec.body_fonts = await renderedFonts(cdp, '[data-aism-body]');
          await cdp.detach();
        })(), 20000, 'cdp fonts');
      } catch (e) { notes.push('cdp-fonts-failed:' + e.message.split('\n')[0]); }
    }

    // Screenshots (animations disabled by Playwright as well, caret hidden).
    const vpPath = path.join(shotsDir, `${id}.viewport.png`);
    const fullPath = path.join(shotsDir, `${id}.full.png`);
    let vpBuf = null;
    try {
      vpBuf = await page.screenshot({ path: vpPath, animations: 'disabled', caret: 'hide', timeout: 30000 });
      rec.viewport_shot = vpPath;
    } catch (e) { notes.push('viewport-shot-failed:' + e.message.split('\n')[0]); }
    try {
      await page.screenshot({ path: fullPath, fullPage: true, animations: 'disabled', caret: 'hide', timeout: 45000 });
      rec.full_shot = fullPath;
    } catch (e) { notes.push('full-shot-failed:' + e.message.split('\n')[0]); }

    // Sampled background for gradient / image backdrops.
    if (m && m.bg && m.bg.kind !== 'solid' && m.bg.kind !== 'browser-default') {
      if (vpBuf && m.bg.sampleRect) {
        try {
          const d = dominantColour(vpBuf, m.bg.sampleRect);
          rec.bg_sampled = d;
        } catch (e) { notes.push('bg-sample-failed:' + e.message); }
      } else notes.push('bg-sample-unavailable');
    }
  } finally {
    await withTimeout(context.close(), 10000, 'context.close').catch((e) => notes.push('context-close:' + e.message));
  }
  rec.requests = requests;
  rec.failed_requests = failed;
  rec.console_errors = consoleErrors;
  rec.page_errors = pageErrors;
  rec.notes = notes;
  return rec;
}

function flatten(rec, cols) {
  const m = rec.raw || {};
  const notes = [...(rec.notes || []), ...((m.notes) || [])];
  const id = parseId(rec.id);
  if (id.bad) notes.push('id-not-model__prompt__run[__arm]');
  const row = { id: rec.id, model: id.model, prompt: id.prompt, run: id.run, arm: id.arm, file_bytes: rec.file_bytes, source_notes: rec.source_notes };
  if (rec.fatal) notes.push('fatal:' + rec.fatal);
  row.color_scheme = m.colorSchemeDark === undefined ? 'na' : m.colorSchemeDark ? 'dark' : 'light';
  row.compat_mode = m.compatMode || 'na';
  if (m.compatMode === 'BackCompat' && rec.file_bytes > 0) notes.push('quirks-mode-rendering(no-doctype-first)');

  // Background
  let bgHex = null;
  if (m.bg) {
    if (m.bg.kind === 'solid') { bgHex = m.bg.color; row.bg_source = 'css-solid'; }
    else if (m.bg.kind === 'browser-default') { bgHex = m.bg.color; row.bg_source = 'browser-default'; notes.push('bg:no-css-background,browser-paints-white'); }
    else if (rec.bg_sampled) { if (m.bg.overGradient) notes.push('bg:image-under-translucent-gradient'); bgHex = rec.bg_sampled.hex; row.bg_source = m.bg.kind === 'gradient' ? 'gradient' : 'image'; notes.push(`bg:${m.bg.kind}-sampled-from-screenshot(${m.bg.element}${m.bg.where ? ' ' + m.bg.where : ''},bin-share=${rec.bg_sampled.share.toFixed(2)})`); }
    else { row.bg_source = 'na'; notes.push('bg:' + m.bg.kind + '-but-no-sample'); }
    row.bg_coverage = m.bg.coverage !== undefined ? m.bg.coverage.toFixed(2) : '';
    if (m.bg.overlays && m.bg.kind === 'solid') notes.push(`bg:composited-${m.bg.overlays}-translucent-layer(s)`);
  } else if (rec.viewport_shot && fs.existsSync(rec.viewport_shot)) {
    // DOM resolution failed entirely: fall back to the dominant colour of the whole viewport.
    try {
      const d = dominantColour(fs.readFileSync(rec.viewport_shot), { x: 0, y: 0, x2: VIEWPORT.width, y2: VIEWPORT.height });
      bgHex = d.hex; row.bg_source = 'sampled'; notes.push('bg:dom-resolution-failed,whole-viewport-sampled');
    } catch { row.bg_source = 'na'; }
  } else row.bg_source = 'na';
  row.bg_color = bgHex || 'na';
  if (bgHex) {
    const L = luminance(hexToRgb(bgHex));
    row.bg_hue_bucket = hueBucket(bgHex);
    row.bg_hue_family = hueFamily(bgHex);
    row.bg_lightness = L.lightness.toFixed(3);
    row.bg_luminance = L.Y.toFixed(3);
    row.theme = L.lightness >= 0.5 ? 'light' : 'dark';
  } else { row.bg_hue_bucket = row.bg_hue_family = row.bg_lightness = row.bg_luminance = row.theme = 'na'; }

  // Accent
  const ac = m.accent || {};
  row.accent_color = ac.color || 'na';
  row.accent_source = ac.source || 'na';
  row.accent_gradient = ac.source ? yn(!!ac.gradient) : 'na';
  row.accent_hue_bucket = ac.color ? hueBucket(ac.color) : 'na';
  row.accent_hue_family = ac.color ? hueFamily(ac.color) : 'na';
  row.primary_button_text = ac.source === 'hero-button' || ac.source === 'nav-button' ? ac.text : 'na';

  // Gradients
  if (m.gradient) {
    row.gradient_present = yn(m.gradient.viewport.length > 0);
    row.gradient_where = m.gradient.viewport.sort();
    row.gradient_fullpage = yn(m.gradient.fullPage.length > 0);
    row.gradient_fullpage_where = m.gradient.fullPage.sort();
  } else row.gradient_present = row.gradient_fullpage = 'na';

  // h1
  if (m.h1) {
    row.h1_count = m.h1.count;
    if (m.h1.text !== undefined) {
      row.h1_text = m.h1.text;
      row.h1_in_viewport = yn(m.h1.inViewport);
      row.h1_centered = yn(m.h1.centered);
      row.h1_text_align = m.h1.textAlign;
      row.h1_font_declared = m.h1.fontDeclared || 'na';
      row.h1_font_rendered = rec.h1_fonts && rec.h1_fonts.length ? familyOf(rec.h1_fonts[0].family) : 'na';
      if (rec.h1_fonts && rec.h1_fonts.length > 1) notes.push('h1-fonts:' + rec.h1_fonts.map((f) => `${f.family}(${f.glyphs})`).join('+'));
    } else { row.h1_text = 'na'; row.h1_in_viewport = 'na'; row.h1_centered = 'na'; row.h1_text_align = 'na'; row.h1_font_declared = 'na'; row.h1_font_rendered = 'na'; }
  } else { for (const k of ['h1_count', 'h1_text', 'h1_in_viewport', 'h1_centered', 'h1_text_align', 'h1_font_declared', 'h1_font_rendered']) row[k] = 'na'; }
  if (m.bodyFont && !m.bodyFont.usedBodyFallback) {
    row.body_font_declared = m.bodyFont.declared || 'na';
    row.body_font_rendered = rec.body_fonts && rec.body_fonts.length ? familyOf(rec.body_fonts[0].family) : 'na';
  } else row.body_font_declared = row.body_font_rendered = 'na';

  // External fonts
  const reqs = rec.requests || [];
  const ext = reqs.filter((r) => /^https?:/.test(r.url));
  if (m.fonts) {
    const fams = new Set(m.fonts.googleFamilies);
    for (const f of m.fonts.remoteFaces) fams.add(f.family);
    // Families loaded from cross-origin CSS we cannot read (e.g. Font Awesome on cdnjs):
    // if any font file came from outside, add every loaded family not defined by a data: URI.
    const extFontReq = ext.some((r) => r.type === 'font');
    if (extFontReq && m.fonts.unreadableSheets > 0) for (const f of m.fonts.loadedFamilies) if (!m.fonts.dataUriFaces.includes(f)) fams.add(f);
    row.external_fonts = [...fams].filter(Boolean);
    const hosts = new Set();
    for (const r of ext) { const h = hostOf(r.url); if (r.type === 'font' || FONT_CSS_HOSTS.includes(h)) hosts.add(h); }
    for (const f of m.fonts.remoteFaces) f.remoteHosts.forEach((h) => hosts.add(h));
    for (const l of [...m.fonts.fontLinks, ...m.fonts.imports]) { const h = hostOf(l); if (h && FONT_CSS_HOSTS.includes(h)) hosts.add(h); }
    row.external_font_hosts = [...hosts].sort();
    if (m.fonts.fontLinks.some((l) => /typekit|fontshare|rsms|cdnfonts/.test(l)) && !m.fonts.googleFamilies.length) notes.push('external-fonts:non-google-font-css-families-not-parsed');
  } else row.external_fonts = row.external_font_hosts = 'na';

  // Buttons
  if (m.buttons) {
    row.buttons_in_viewport = m.buttons.inViewport;
    row.buttons_in_viewport_nav = m.buttons.nav;
    row.buttons_in_viewport_nonnav = m.buttons.nonNav;
    row.buttons_in_viewport_mockup = m.buttons.inMockup ?? 'na';
  } else row.buttons_in_viewport = row.buttons_in_viewport_nav = row.buttons_in_viewport_nonnav = row.buttons_in_viewport_mockup = 'na';

  // Cards
  const blocks = (m.sections && m.sections.blocks) || [];
  let rowKinds = [];
  if (m.cards) {
    const rows = m.cards.rows;
    rowKinds = rows.map((r) => ({ ...r, ...classifyRow(r, blocks) }));
    rec.card_rows = rowKinds; // audit trail in JSONL
    const sizes = rows.map((r) => r.size);
    const three = rowKinds.filter((r) => r.size === 3);
    const has = (k) => three.some((r) => r.kind === k);
    row.three_card_grid = yn(has('feature'));
    row.max_cards_in_row = sizes.length ? Math.max(...sizes) : 0;
    row.rows_3up = sizes.filter((n) => n === 3).length;
    row.rows_4up = sizes.filter((n) => n === 4).length;
    const featContainers = [...new Set(three.filter((r) => r.kind === 'feature').map((r) => r.containerId))];
    row.grid_cards_total = featContainers.length ? featContainers.map((c) => rowKinds.filter((r) => r.containerId === c && r.kind === 'feature').reduce((a, r) => a + r.size, 0)) : '0';
    row.three_up_any = yn(three.length > 0);
    row.three_up_pricing = yn(has('pricing'));
    row.three_up_testimonials = yn(has('testimonials'));
    row.three_up_steps = yn(has('steps'));
    row.three_up_in_mockup = yn(has('in_mockup'));
    row.three_up_stats = yn(has('stats'));
    row.three_up_menu = yn(has('menu'));
    row.three_up_team = yn(has('team'));
    row.three_up_gallery = yn(has('gallery'));
  } else for (const k of ['three_card_grid', 'max_cards_in_row', 'rows_3up', 'rows_4up', 'grid_cards_total', 'three_up_any', 'three_up_pricing', 'three_up_testimonials', 'three_up_steps', 'three_up_in_mockup', 'three_up_stats', 'three_up_menu', 'three_up_team', 'three_up_gallery']) row[k] = 'na';
  row.mockup_present = m.mockups ? yn(m.mockups.length > 0) : 'na';

  // Emoji / icons
  if (m.emoji) {
    row.emoji_icon_count = m.emoji.iconCount;
    row.emoji_total = m.emoji.total;
  } else row.emoji_icon_count = row.emoji_total = 'na';
  if (m.icons) {
    row.svg_icon_count = m.icons.svgIcons; row.svg_total = m.icons.svgTotal; row.icon_font_count = m.icons.iconFont; row.img_icon_count = m.icons.imgIcons;
  } else row.svg_icon_count = row.svg_total = row.icon_font_count = row.img_icon_count = 'na';

  // Sections
  if (m.sections) {
    row.section_order = m.sections.labels.join(' | ');
    let norm;
    if (m.sections.via === 'h2' && blocks.length === m.sections.labels.length) {
      const labs = blocks.map((b, i) => sectionLabel(b, i, blocks, rowKinds));
      rec.section_labels = labs.map((l, i) => ({ heading: blocks[i].text, eyebrow: blocks[i].eyebrow, ...l }));
      norm = labs.map((l) => l.label);
    } else norm = m.sections.labels.map(normaliseSection);
    if (m.h1 && m.h1.inViewport) norm.unshift('hero');
    if (m.sections.footer) norm.push('footer');
    row.section_order_norm = norm.join(' | ');
    row.n_h2 = m.sections.h2Count;
    row.n_sections = m.sections.sectionElements;
  } else row.section_order = row.section_order_norm = row.n_h2 = row.n_sections = 'na';

  // Frameworks / network (na when the page never produced a measurement record)
  if (!rec.raw && rec.fatal) {
    for (const k of ['frameworks', 'external_requests', 'external_hosts', 'has_external_images', 'external_image_hosts', 'failed_requests', 'nav_present', 'hero_has_image', 'word_count', 'console_error_count', 'page_error_count']) row[k] = 'na';
    row.load_error = rec.load_error || ''; row.measure_notes = notes; row.viewport_shot = ''; row.full_shot = '';
    return cols.map((c) => csvCell(row[c] === undefined ? 'na' : row[c])).join(',');
  }
  const allUrls = [...((m.staticUrls) || []), ...reqs.map((r) => r.url)];
  row.frameworks = detectFrameworks(allUrls);
  row.external_requests = ext.length;
  row.external_hosts = [...new Set(ext.map((r) => hostOf(r.url)))].sort();
  const imgReqs = ext.filter((r) => r.type === 'image' || /\.(png|jpe?g|webp|gif|avif|svg)(\?|$)/i.test(r.url) && r.type !== 'stylesheet' && r.type !== 'script' && r.type !== 'font');
  row.has_external_images = yn(imgReqs.length > 0);
  row.external_image_hosts = [...new Set(imgReqs.map((r) => hostOf(r.url)))].sort();
  row.failed_requests = (rec.failed_requests || []).length;

  row.nav_present = m.nav ? yn(m.nav.present) : 'na';
  if (m.nav && m.nav.via === 'top-bar-with-3+-links') notes.push('nav:no-<nav>,detected-top-bar');
  row.hero_has_image = m.heroImage === undefined ? 'na' : yn(!!m.heroImage);
  if (m.heroImage) notes.push('hero-image-kind:' + m.heroImage);
  row.word_count = m.wordCount ?? 'na';

  row.load_error = rec.load_error || '';
  row.console_error_count = (rec.console_errors || []).length;
  row.page_error_count = (rec.page_errors || []).length;
  row.measure_notes = notes;
  row.viewport_shot = rec.viewport_shot ? path.basename(rec.viewport_shot) : '';
  row.full_shot = rec.full_shot ? path.basename(rec.full_shot) : '';
  return cols.map((c) => csvCell(row[c] === undefined ? 'na' : row[c])).join(',');
}

// One browser process per page: a page that hangs its renderer (e.g. an infinite JS
// loop) is killed with its process and cannot affect any other page.
async function launchServer() {
  return chromium.launchServer({
    args: ['--disable-gpu', '--font-render-hinting=none'],
    env: { ...process.env, LD_LIBRARY_PATH: `${LIBS}:${process.env.LD_LIBRARY_PATH || ''}`, FONTCONFIG_FILE: FONTCONFIG },
    timeout: 300000, // first launch after a font change rebuilds the fontconfig cache
  });
}

async function measureIsolated(file, id, shotsDir) {
  const server = await launchServer();
  try {
    const browser = await chromium.connect(server.wsEndpoint(), { timeout: 60000 });
    return await withTimeout(measureOne(browser, file, id, shotsDir), PAGE_HARD_TIMEOUT_MS, 'page');
  } finally {
    await withTimeout(server.close(), 10000, 'server.close').catch(() => server.kill().catch(() => {}));
  }
}

async function main() {
  const args = parseArgs(process.argv);
  fs.mkdirSync(args.shots, { recursive: true });
  let ids = fs.readdirSync(args.pages).filter((f) => f.toLowerCase().endsWith('.html')).map((f) => f.slice(0, -5)).sort();
  if (args.only) {
    const missing = args.only.filter((i) => !ids.includes(i));
    if (missing.length) console.error('warning: not found:', missing.join(','));
    ids = ids.filter((i) => args.only.includes(i));
  }
  console.error(`measuring ${ids.length} page(s), concurrency ${args.concurrency}, ${os.freemem() >> 20} MB free`);
  const results = new Map();
  let next = 0;
  const t0 = Date.now();

  async function worker() {
    while (next < ids.length) {
      const id = ids[next++];
      const file = path.join(args.pages, `${id}.html`);
      const t = Date.now();
      let rec;
      for (let attempt = 1; attempt <= 2; attempt++) {
        try {
          rec = await measureIsolated(file, id, args.shots);
          if (attempt > 1) rec.notes.push('succeeded-on-retry');
          break;
        } catch (e) {
          const msg = e.message.split('\n')[0];
          const fb = fs.existsSync(file) ? fs.readFileSync(file) : null;
          rec = { id, file, fatal: (attempt > 1 ? 'attempt-2:' : '') + msg, file_bytes: fb ? fb.length : 'na', notes: [], source_notes: fb ? sourceNotesFor(fb) : ['file-missing'] };
          if (!fb) break;
          console.error(`  ${id} attempt ${attempt} failed: ${msg}`);
        }
      }
      rec.elapsed_ms = Date.now() - t;
      results.set(id, rec);
      console.error(`[${results.size}/${ids.length}] ${id} ${rec.elapsed_ms}ms${rec.fatal ? ' FATAL ' + rec.fatal : ''}`);
    }
  }
  await Promise.all(Array.from({ length: Math.min(args.concurrency, ids.length || 1) }, worker));

  const env = { tool_version: TOOL_VERSION, chromium: 'chromium_headless_shell-1255 via playwright-core 1.65.1', viewport: VIEWPORT, deviceScaleFactor: 1, colorScheme: 'browser default (light)', loadTimeoutMs: LOAD_TIMEOUT_MS, fontconfig: FONTCONFIG };
  const lines = [COLUMNS.join(',')];
  const jl = [];
  for (const id of ids) {
    const rec = results.get(id);
    let line;
    try { line = flatten(rec, COLUMNS); } catch (e) { line = flatten({ id, fatal: 'flatten:' + e.message, notes: [], source_notes: [] }, COLUMNS); }
    lines.push(line);
    jl.push(JSON.stringify({ ...rec, env }));
  }
  fs.writeFileSync(args.out, lines.join('\n') + '\n');
  fs.writeFileSync(args.jsonl, jl.join('\n') + '\n');
  console.error(`wrote ${args.out} and ${args.jsonl} in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
}

main().catch((e) => { console.error(e); process.exit(1); });
