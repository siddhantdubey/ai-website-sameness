// In-page measurement. This function is serialised by Playwright and run inside
// the page, so it must be fully self-contained (no imports, no closures).
// It returns raw facts; bucketing and CSV formatting happen in Node.
// Every block is wrapped so one failure becomes a note, not a crash.

export function measureInPage(opts) {
  const VW = window.innerWidth, VH = window.innerHeight;
  const VAREA = VW * VH;
  const notes = [];
  const out = { notes };
  const doc = document;
  const body = doc.body;

  // ---------- colour parsing ----------
  const cv = doc.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const cache = new Map();
  function parseColor(str) {
    if (!str) return null;
    if (cache.has(str)) return cache.get(str);
    let res = null;
    const m = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+)(%?))?\s*\)$/.exec(str);
    if (m) {
      let a = m[4] === undefined ? 1 : parseFloat(m[4]);
      if (m[5] === '%') a /= 100;
      res = { r: +m[1], g: +m[2], b: +m[3], a };
    } else if (str === 'transparent') {
      res = { r: 0, g: 0, b: 0, a: 0 };
    } else {
      // oklch(), lab(), color(), color-mix() ... let the canvas resolve to sRGB.
      cx.fillStyle = '#010203'; cx.fillStyle = str;
      const first = cx.fillStyle;
      cx.fillStyle = '#030201'; cx.fillStyle = str;
      if (first === '#010203' && cx.fillStyle === '#030201') res = null; // unparseable
      else {
        cx.clearRect(0, 0, 1, 1); cx.fillRect(0, 0, 1, 1);
        const d = cx.getImageData(0, 0, 1, 1).data;
        res = { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
      }
    }
    cache.set(str, res);
    return res;
  }
  const hex = (c) => '#' + [c.r, c.g, c.b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
  const over = (top, a, base) => ({ r: top.r * a + base.r * (1 - a), g: top.g * a + base.g * (1 - a), b: top.b * a + base.b * (1 - a), a: 1 });
  const dist = (p, q) => Math.hypot(p.r - q.r, p.g - q.g, p.b - q.b);
  const COLOR_TOKEN = /(rgba?\([^)]*\)|hsla?\([^)]*\)|oklch\([^)]*\)|oklab\([^)]*\)|lab\([^)]*\)|lch\([^)]*\)|color\([^)]*\)|#[0-9a-fA-F]{3,8}\b|\btransparent\b)/g;
  function gradientStops(bgImage) {
    const i = bgImage.indexOf('gradient(');
    if (i < 0) return [];
    return (bgImage.slice(i).match(COLOR_TOKEN) || []).map(parseColor).filter(Boolean);
  }
  const collapse = (s) => String(s || '').replace(/\s+/g, ' ').trim();

  // ---------- geometry / visibility ----------
  const csCache = new Map();
  const cs = (el) => { let s = csCache.get(el); if (!s) { s = getComputedStyle(el); csCache.set(el, s); } return s; };
  const opCache = new Map();
  function cumOpacity(el) {
    if (!el || el.nodeType !== 1) return 1;
    if (opCache.has(el)) return opCache.get(el);
    const v = parseFloat(cs(el).opacity || '1') * cumOpacity(el.parentElement);
    opCache.set(el, v);
    return v;
  }
  function visible(el) {
    if (!el || el.nodeType !== 1) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = cs(el);
    if (s.visibility === 'hidden' || s.visibility === 'collapse' || s.display === 'none') return false;
    if (cumOpacity(el) < 0.05) return false;
    return true;
  }
  function vpArea(r) {
    const w = Math.max(0, Math.min(r.right, VW) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, VH) - Math.max(r.top, 0));
    return w * h;
  }
  const inViewport = (r) => r.bottom > 0 && r.top < VH && r.right > 0 && r.left < VW && r.width > 0 && r.height > 0;
  const rectOf = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }; };

  function inNav(el) {
    if (el.closest('nav,[role=navigation]')) return true;
    const hdr = el.closest('header');
    if (hdr && hdr.getBoundingClientRect().height <= 200) return true;
    for (let a = el; a && a !== body && a.nodeType === 1; a = a.parentElement) {
      const s = cs(a);
      if (s.position === 'fixed' || s.position === 'sticky') {
        const r = a.getBoundingClientRect();
        if (r.top <= 10 && r.height <= 200 && r.width >= VW * 0.5) return true;
      }
    }
    return false;
  }

  if (!body) {
    notes.push('no-body-element');
    out.empty = true;
    return out;
  }
  const all = [...body.querySelectorAll('*')];

  // Make every element hit-testable while we probe the paint stack
  // (decorative overlays often have pointer-events:none). Removed at the end.
  const pe = doc.createElement('style');
  pe.textContent = '*,*::before,*::after{pointer-events:auto!important}';
  doc.documentElement.appendChild(pe);

  // ---------- paint-stack resolution ----------
  const htmlCS = cs(doc.documentElement), bodyCS = cs(body);
  const htmlBg = parseColor(htmlCS.backgroundColor) || { r: 0, g: 0, b: 0, a: 0 };
  const bodyBg = parseColor(bodyCS.backgroundColor) || { r: 0, g: 0, b: 0, a: 0 };
  const htmlHasImg = htmlCS.backgroundImage && htmlCS.backgroundImage !== 'none';
  const bodyHasImg = bodyCS.backgroundImage && bodyCS.backgroundImage !== 'none';
  const bodyPropagates = htmlBg.a === 0 && !htmlHasImg; // CSS: body background paints the canvas
  function canvasLayer() {
    // What the root canvas paints.
    const src = bodyPropagates ? bodyCS : htmlCS;
    const srcEl = bodyPropagates ? body : doc.documentElement;
    const img = src.backgroundImage || 'none';
    if (img.includes('url(')) return { kind: 'image', el: srcEl, where: bodyPropagates ? 'body' : 'html' };
    if (img.includes('gradient(')) return { kind: 'gradient', el: srcEl, where: bodyPropagates ? 'body' : 'html' };
    const c = bodyPropagates ? bodyBg : htmlBg;
    if (c.a === 0) return { kind: 'browser-default', color: { r: 255, g: 255, b: 255, a: 1 }, el: null, where: 'canvas' };
    return { kind: 'solid', color: over(c, c.a, { r: 255, g: 255, b: 255 }), el: srcEl, where: bodyPropagates ? 'body' : 'html' };
  }
  function pseudoLayers(el) {
    const res = [];
    const r = el.getBoundingClientRect();
    for (const p of ['::after', '::before']) {
      const s = getComputedStyle(el, p);
      if (!s || s.content === 'none' || s.content === 'normal' || s.display === 'none') continue;
      const w = parseFloat(s.width), h = parseFloat(s.height);
      if (!(w >= r.width * 0.9 && h >= r.height * 0.9)) continue; // only full-cover pseudo layers
      const op = parseFloat(s.opacity || '1');
      const img = s.backgroundImage || 'none';
      const c = parseColor(s.backgroundColor);
      res.push({ img, c, op, pseudo: p });
    }
    return res;
  }
  // Walk the elements under (x,y) from top to bottom; translucent colour layers are
  // composited; stop at the first opaque colour, gradient, image or media element.
  function resolveAt(x, y, minArea, exclude) {
    const stack = doc.elementsFromPoint(x, y);
    const translucent = [];
    let stop = null;
    let pendingGrad = null; // translucent gradient seen above; keep walking to find what is under it
    const gradOpaque = (img) => { const st = gradientStops(img); return st.length > 0 && st.every((c) => c.a >= 0.95); };
    const hitGradient = (el, where, img) => {
      if (gradOpaque(img)) return { kind: 'gradient', el, where };
      if (!pendingGrad) pendingGrad = { kind: 'gradient', el, where };
      return null;
    };
    for (const el of stack) {
      if (el === pe) continue;
      if (exclude && (exclude === el || exclude.contains(el))) continue;
      if (el === doc.documentElement) continue;
      if (el === body && bodyPropagates) continue;
      const area = vpArea(el.getBoundingClientRect());
      if (el !== body && area < minArea) continue;
      const s = cs(el);
      if (s.visibility === 'hidden') continue;
      const op = cumOpacity(el);
      if (op < 0.05) continue;
      const tag = el.tagName.toLowerCase();
      if (['img', 'video', 'canvas', 'iframe', 'picture', 'object', 'embed'].includes(tag) || (tag === 'svg' && area >= VAREA * 0.1)) {
        stop = { kind: 'image', el, where: tag }; break;
      }
      for (const p of pseudoLayers(el)) {
        if (p.img.includes('url(')) { stop = { kind: 'image', el, where: p.pseudo }; break; }
        if (p.img.includes('gradient(')) { stop = hitGradient(el, p.pseudo, p.img); if (stop) break; continue; }
        if (p.c && p.c.a * p.op * op >= 0.95) { stop = { kind: 'solid', color: p.c, el, where: p.pseudo }; break; }
        if (p.c && p.c.a * p.op * op > 0.02) translucent.push({ c: p.c, a: p.c.a * p.op * op });
      }
      if (stop) break;
      const img = s.backgroundImage || 'none';
      if (img.includes('url(')) { stop = { kind: 'image', el, where: 'css-url' }; break; }
      if (img.includes('gradient(')) {
        stop = hitGradient(el, 'css-gradient', img);
        if (stop) break;
      }
      const c = parseColor(s.backgroundColor);
      if (c && c.a * op >= 0.95) { stop = { kind: 'solid', color: c, el, where: 'element' }; break; }
      if (c && c.a * op > 0.02) translucent.push({ c, a: c.a * op });
    }
    if (!stop) stop = canvasLayer();
    if (pendingGrad) {
      // A translucent gradient lies over whatever we stopped at: an image stays 'image'
      // (the photo dominates); anything else is reported as the gradient.
      if (stop.kind === 'image') return { ...stop, overGradient: true, overlays: translucent.length + 1 };
      return { ...pendingGrad, overlays: translucent.length };
    }
    if (stop.kind === 'solid' || stop.kind === 'browser-default') {
      let col = stop.color;
      for (let i = translucent.length - 1; i >= 0; i--) col = over(translucent[i].c, translucent[i].a, col);
      return { ...stop, color: col, overlays: translucent.length };
    }
    return { ...stop, overlays: translucent.length };
  }
  const elIds = new Map();
  const elId = (el) => { if (!el) return 'canvas'; if (!elIds.has(el)) elIds.set(el, elIds.size); return elIds.get(el); };
  function describe(el) {
    if (!el) return 'canvas';
    let d = el.tagName.toLowerCase();
    if (el.id) d += '#' + el.id;
    if (typeof el.className === 'string' && el.className.trim()) d += '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.');
    return d;
  }

  // ---------- effective page background ----------
  let bgWinner = null;
  try {
    const votes = new Map();
    const MIN = VAREA * 0.30;
    let n = 0;
    for (let i = 0; i < 10; i++) for (let j = 0; j < 8; j++) {
      const x = VW * (0.05 + i * 0.1), y = VH * (0.03 + j * (0.94 / 7));
      const r = resolveAt(x, y, MIN, null);
      n++;
      const key = r.kind === 'solid' || r.kind === 'browser-default' ? `${r.kind}:${hex(r.color)}` : `${r.kind}:${elId(r.el)}:${r.where}`;
      const v = votes.get(key) || { count: 0, r };
      v.count++; votes.set(key, v);
    }
    const ranked = [...votes.values()].sort((a, b) => b.count - a.count);
    const w = ranked[0];
    bgWinner = w.r;
    out.bg = {
      kind: w.r.kind,
      color: w.r.color ? hex(w.r.color) : null,
      coverage: w.count / n,
      element: describe(w.r.el),
      where: w.r.where,
      overlays: w.r.overlays || 0,
      overGradient: !!w.r.overGradient,
      sampleRect: w.r.el && w.r.kind !== 'solid' ? (() => { const r = w.r.el.getBoundingClientRect(); return { x: Math.max(0, r.left), y: Math.max(0, r.top), x2: Math.min(VW, r.right), y2: Math.min(VH, r.bottom) }; })() : null,
      runnersUp: ranked.slice(1, 3).map((v) => ({ kind: v.r.kind, color: v.r.color ? hex(v.r.color) : null, element: describe(v.r.el), share: v.count / n })),
    };
    if ((w.r.el === doc.documentElement || w.r.el === body) && w.r.kind !== 'solid') out.bg.sampleRect = { x: 0, y: 0, x2: VW, y2: VH };
  } catch (e) { notes.push('bg-error:' + e.message); }

  // ---------- button-like elements ----------
  function buttonLike(el) {
    const tag = el.tagName.toLowerCase();
    const isCand = tag === 'button' || tag === 'a' || el.getAttribute('role') === 'button' || (tag === 'input' && /^(submit|button)$/i.test(el.type));
    if (!isCand || !visible(el)) return null;
    const r = el.getBoundingClientRect();
    if (r.width > 600 || r.height > 120 || r.width < 16 || r.height < 16) return null;
    const s = cs(el);
    const bg = parseColor(s.backgroundColor) || { a: 0 };
    const op = cumOpacity(el);
    const img = s.backgroundImage || 'none';
    const hasGrad = img.includes('gradient(');
    const bw = ['Top', 'Right', 'Bottom', 'Left'].map((k) => (s[`border${k}Style`] !== 'none' && s[`border${k}Style`] !== 'hidden') ? parseFloat(s[`border${k}Width`]) : 0);
    const bc = parseColor(s.borderTopColor) || { a: 0 };
    const hasBorder = bw.every((w) => w >= 1) && bc.a >= 0.3;
    const hasBg = bg.a * op >= 0.1 || hasGrad;
    const padX = parseFloat(s.paddingLeft) + parseFloat(s.paddingRight);
    const padY = parseFloat(s.paddingTop) + parseFloat(s.paddingBottom);
    const padded = padX >= 12 || padY >= 8 || r.height >= 28;
    if (!(hasBg || hasBorder) || !padded) return null;
    return { el, r, s, bg, op, hasGrad, img, hasBorder, hasBg };
  }
  let buttons = [];
  try {
    const seen = [];
    for (const el of all) {
      const b = buttonLike(el);
      if (!b) continue;
      if (seen.some((o) => o.contains(el) || el.contains(o))) continue; // nested button-likes count once
      seen.push(el);
      buttons.push(b);
    }
  } catch (e) { notes.push('buttons-error:' + e.message); }
  const vpButtons = buttons.filter((b) => inViewport(b.r));
  for (const b of vpButtons) b.nav = inNav(b.el);
  out.buttons = {
    inViewport: vpButtons.length,
    nav: vpButtons.filter((b) => b.nav).length,
    nonNav: vpButtons.filter((b) => !b.nav).length,
    totalPage: buttons.length,
  };

  // ---------- primary button / accent ----------
  try {
    const filled = [];
    for (const b of vpButtons) {
      const cxp = Math.min(Math.max(b.r.left + b.r.width / 2, 1), VW - 1);
      const cyp = Math.min(Math.max(b.r.top + b.r.height / 2, 1), VH - 1);
      const back = resolveAt(cxp, cyp, b.r.width * b.r.height * 1.5, b.el);
      let isFilled = false, color = null, gradient = false;
      if (b.hasGrad) {
        const stops = gradientStops(b.img);
        const firstOpaque = stops.find((c) => c.a > 0.05);
        if (stops.length) { isFilled = true; gradient = true; color = stops[0].a > 0.05 ? stops[0] : firstOpaque; if (stops[0].a <= 0.05) b.firstStopTransparent = true; }
      }
      if (!isFilled && b.bg.a * b.op >= 0.5) {
        const backCol = back.color || null; // null when the backdrop is an image/gradient
        if (!backCol || dist(b.bg, backCol) >= 40) { isFilled = true; color = b.bg.a < 1 && backCol ? over(b.bg, b.bg.a, backCol) : b.bg; }
      }
      if (isFilled && color) filled.push({ ...b, color, gradient, area: b.r.width * b.r.height, backdrop: back.color ? hex(back.color) : back.kind });
    }
    const pick = (list) => {
      if (!list.length) return null;
      const maxA = Math.max(...list.map((x) => x.area));
      return list.find((x) => x.area >= maxA * 0.98); // largest; near-ties -> first in DOM order
    };
    let chosen = pick(filled.filter((b) => !b.nav)), source = 'hero-button';
    if (!chosen) { chosen = pick(filled.filter((b) => b.nav)); source = 'nav-button'; }
    if (chosen) {
      out.accent = { color: hex(chosen.color), source, gradient: chosen.gradient, text: collapse(chosen.el.innerText || chosen.el.value || chosen.el.getAttribute('aria-label') || ''), rect: rectOf(chosen.el), element: describe(chosen.el), backdrop: chosen.backdrop };
      if (chosen.firstStopTransparent) notes.push('accent-gradient-first-stop-transparent:used-first-opaque-stop');
      chosen.el.setAttribute('data-aism-primary', '1');
      if (source === 'nav-button') notes.push('accent:no-filled-hero-button,used-nav-button');
    } else {
      const outlined = vpButtons.filter((b) => !b.nav);
      if (outlined.length) notes.push('accent:hero-has-only-unfilled-buttons');
      // Fallback: most common chromatic link / heading text colour in the page.
      const counts = new Map();
      const btnSet = new Set(buttons.map((b) => b.el));
      for (const el of body.querySelectorAll('a,h1,h2,h3')) {
        if (btnSet.has(el) || !visible(el) || !collapse(el.textContent)) continue;
        const c = parseColor(cs(el).color);
        if (!c || c.a < 0.5) continue;
        const R = c.r / 255, G = c.g / 255, B = c.b / 255;
        const mx = Math.max(R, G, B), mn = Math.min(R, G, B), l = (mx + mn) / 2;
        const sat = mx === mn ? 0 : (mx - mn) / (1 - Math.abs(2 * l - 1));
        if (l <= 0.08 || sat < 0.12 || (l >= 0.95)) continue; // neutral
        const k = hex(c); counts.set(k, (counts.get(k) || 0) + 1);
      }
      const best = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
      if (best) { out.accent = { color: best[0], source: 'link-colour-fallback', gradient: false, text: null, uses: best[1] }; notes.push('accent:no-filled-button,used-link-heading-colour'); }
      else { out.accent = { color: null, source: 'none', gradient: false, text: null }; notes.push('accent:none-determinable'); }
    }
  } catch (e) { notes.push('accent-error:' + e.message); }

  // ---------- gradients ----------
  try {
    const btnEls = buttons.map((b) => b.el);
    const inBtn = (el) => btnEls.some((b) => b === el || b.contains(el));
    const vpWhere = new Set(), fullWhere = new Set();
    const vpList = [];
    const consider = (el, s, r, pseudo) => {
      const img = s.backgroundImage || 'none';
      if (!/gradient\(/.test(img)) return;
      const clip = s.backgroundClip || s.webkitBackgroundClip || '';
      const w = pseudo ? (parseFloat(s.width) || r.width) : r.width;
      const h = pseudo ? (parseFloat(s.height) || r.height) : r.height;
      if (w < 16 || h < 16) return; // trivial (hairlines, dots)
      let where;
      if (/text/.test(clip) || /text/.test(s.webkitBackgroundClip || '')) where = 'text';
      else if (inBtn(el)) where = 'button';
      else if (el === doc.documentElement || el === body || vpArea(r) >= VAREA * 0.25 || (bgWinner && bgWinner.el === el)) where = 'hero-bg';
      else where = 'other';
      fullWhere.add(where);
      if (inViewport(r) || el === doc.documentElement || el === body) {
        vpWhere.add(where);
        vpList.push({ el: describe(el) + (pseudo || ''), where, size: `${Math.round(w)}x${Math.round(h)}` });
      }
    };
    for (const el of [doc.documentElement, body, ...all]) {
      if (el === pe) continue;
      const s = cs(el);
      if (s.display === 'none') continue;
      const r = el.getBoundingClientRect();
      const isRoot = el === doc.documentElement || el === body;
      if (!isRoot && !visible(el)) continue;
      consider(el, s, isRoot ? { left: 0, top: 0, right: VW, bottom: VH, width: VW, height: VH } : r);
      for (const p of ['::before', '::after']) {
        const ps = getComputedStyle(el, p);
        if (ps && ps.content !== 'none' && ps.content !== 'normal' && ps.display !== 'none' && parseFloat(ps.opacity || '1') > 0.05) consider(el, ps, r, p);
      }
    }
    out.gradient = { viewport: [...vpWhere], fullPage: [...fullWhere], viewportList: vpList.slice(0, 20) };
  } catch (e) { notes.push('gradient-error:' + e.message); }

  // ---------- h1 ----------
  try {
    const h1s = [...body.querySelectorAll('h1')];
    const vis = h1s.filter(visible);
    out.h1 = { count: h1s.length, visibleCount: vis.length };
    const h1 = vis[0] || null;
    if (!h1) { notes.push(h1s.length ? 'h1:present-but-not-visible' : 'h1:none'); }
    else {
      h1.setAttribute('data-aism-h1', '1');
      const r = h1.getBoundingClientRect();
      const s = cs(h1);
      out.h1.text = collapse(h1.innerText);
      out.h1.inViewport = inViewport(r);
      out.h1.textAlign = s.textAlign;
      out.h1.fontDeclared = (s.fontFamily.split(',')[0] || '').trim().replace(/^["']|["']$/g, '');
      out.h1.fontStack = s.fontFamily;
      out.h1.fontSizePx = parseFloat(s.fontSize);
      // Rendered line boxes of the text.
      const range = doc.createRange(); range.selectNodeContents(h1);
      const rects = [...range.getClientRects()].filter((q) => q.width > 1 && q.height > 1);
      const lines = [];
      for (const q of rects.sort((a, b) => a.top - b.top)) {
        const L = lines.find((l) => Math.abs(l.top - q.top) < Math.max(4, q.height * 0.5));
        if (L) { L.left = Math.min(L.left, q.left); L.right = Math.max(L.right, q.right); }
        else lines.push({ top: q.top, left: q.left, right: q.right });
      }
      const tol = VW * 0.04;
      const mids = lines.map((l) => (l.left + l.right) / 2 - VW / 2);
      out.h1.lines = lines.length;
      out.h1.lineMidOffsetsPx = mids.map((m) => Math.round(m));
      const allMid = lines.length > 0 && mids.every((m) => Math.abs(m) <= tol);
      const minLeft = lines.length ? Math.min(...lines.map((l) => l.left)) : 0;
      const alignCenter = /center/.test(s.textAlign);
      const shrinkWrapped = minLeft >= VW * 0.10;
      out.h1.centered = out.h1.inViewport ? (allMid && (alignCenter || shrinkWrapped)) : false;
      if (!out.h1.inViewport) notes.push('h1:not-in-first-viewport');
      if (alignCenter && !allMid) notes.push('h1:text-align-center-but-not-centred-on-page');
    }
  } catch (e) { notes.push('h1-error:' + e.message); }

  // ---------- body text element (for body font) ----------
  try {
    const exclude = 'nav,header nav,footer,button,a,h1,h2,h3,h4,h5,h6,[role=button],label';
    let bodyEl = null;
    for (const p of body.querySelectorAll('p')) {
      if (!visible(p) || p.closest(exclude)) continue;
      if (collapse(p.innerText).length >= 40) { bodyEl = p; break; }
    }
    if (!bodyEl) {
      for (const el of body.querySelectorAll('p,li,span,div,td,dd,blockquote')) {
        if (!visible(el) || el.closest(exclude)) continue;
        const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ');
        if (collapse(own).length >= 40) { bodyEl = el; notes.push('body-font:no-40-char-<p>,used-' + el.tagName.toLowerCase()); break; }
      }
    }
    if (!bodyEl) { bodyEl = body; notes.push('body-font:no-40-char-text-block,na'); }
    bodyEl.setAttribute('data-aism-body', '1');
    const s = cs(bodyEl);
    out.bodyFont = { declared: (s.fontFamily.split(',')[0] || '').trim().replace(/^["']|["']$/g, ''), stack: s.fontFamily, element: describe(bodyEl), usedBodyFallback: bodyEl === body };
  } catch (e) { notes.push('bodyfont-error:' + e.message); }

  // ---------- fonts declared / loaded ----------
  try {
    const gf = [];
    const fontLinks = [];
    const parseGF = (u) => {
      try {
        const url = new URL(u, location.href);
        if (!/fonts\.(googleapis|bunny)\.(com|net)$/.test(url.host)) return;
        for (const f of url.searchParams.getAll('family')) for (const part of f.split('|')) gf.push(part.split(':')[0].replace(/\+/g, ' ').trim());
      } catch { /* bad url */ }
    };
    for (const l of doc.querySelectorAll('link[href]')) {
      const h = l.getAttribute('href');
      if (/fonts\.googleapis|fonts\.bunny|fontshare|typekit|rsms\.me|cdnfonts/.test(h)) { fontLinks.push(h); parseGF(h); }
    }
    const imports = [];
    for (const st of doc.querySelectorAll('style')) {
      for (const m of st.textContent.matchAll(/@import\s+(?:url\()?\s*['"]?([^'")\s;]+)/g)) { imports.push(m[1]); parseGF(m[1]); }
    }
    const faces = [];
    const walk = (rules) => {
      for (const r of rules) {
        if (r.type === CSSRule.FONT_FACE_RULE) {
          const fam = (r.style.getPropertyValue('font-family') || '').replace(/["']/g, '').trim();
          const src = r.style.getPropertyValue('src') || '';
          const remote = [...src.matchAll(/url\(\s*['"]?(https?:[^'")]+)/g)].map((m) => m[1]);
          const local = /url\(\s*['"]?data:/.test(src) ? 'data-uri' : null;
          faces.push({ family: fam, remoteHosts: [...new Set(remote.map((u) => { try { return new URL(u).host; } catch { return u; } }))], local });
        } else if (r.cssRules) { try { walk(r.cssRules); } catch { /* */ } }
      }
    };
    let unreadable = 0;
    for (const sh of doc.styleSheets) { try { walk(sh.cssRules); } catch { unreadable++; } }
    const loaded = [];
    try { for (const f of doc.fonts) if (f.status === 'loaded') loaded.push(f.family.replace(/["']/g, '')); } catch { /* */ }
    out.fonts = {
      googleFamilies: [...new Set(gf)],
      fontLinks,
      imports,
      remoteFaces: faces.filter((f) => f.remoteHosts.length),
      dataUriFaces: faces.filter((f) => f.local).map((f) => f.family),
      loadedFamilies: [...new Set(loaded)],
      unreadableSheets: unreadable,
    };
  } catch (e) { notes.push('fonts-error:' + e.message); }

  // ---------- static resource URLs (for framework detection) ----------
  try {
    const urls = [];
    for (const l of doc.querySelectorAll('link[href]')) urls.push(l.href);
    for (const sc of doc.querySelectorAll('script[src]')) urls.push(sc.src);
    for (const st of doc.querySelectorAll('style')) for (const m of st.textContent.matchAll(/@import\s+(?:url\()?\s*['"]?([^'")\s;]+)/g)) urls.push(m[1]);
    const inlineScripts = [...doc.querySelectorAll('script:not([src])')].map((s) => s.textContent).join('\n');
    for (const m of inlineScripts.matchAll(/(?:import\s[^'"]*|import\(|from\s)\s*['"](https?:[^'"]+)['"]/g)) urls.push(m[1]);
    const imgUrls = [...doc.querySelectorAll('img[src],source[srcset]')].map((i) => i.currentSrc || i.src || i.getAttribute('srcset')).filter(Boolean);
    out.staticUrls = urls;
    out.imgUrls = imgUrls.slice(0, 200);
  } catch (e) { notes.push('urls-error:' + e.message); }

  // ---------- emoji and icons ----------
  try {
    const seg = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    const isEmoji = (g) => /\p{Emoji_Presentation}/u.test(g) || (/\p{Extended_Pictographic}/u.test(g) && (/️/.test(g) || g.codePointAt(0) >= 0x1F000));
    const graphemes = (t) => [...seg.segment(t)].map((x) => x.segment);
    const text = body.innerText || '';
    out.emoji = { total: graphemes(text).filter(isEmoji).length };
    let iconEls = 0, leadHeadings = 0;
    const iconSamples = [];
    for (const el of all) {
      if (!visible(el)) continue;
      const t = (el.textContent || '').replace(/\s+/g, '');
      if (!t || t.length > 16) continue;
      const g = graphemes(t);
      if (g.length >= 1 && g.length <= 2 && g.every(isEmoji)) {
        // count only the innermost element carrying this text
        const child = [...el.children].find((c) => (c.textContent || '').replace(/\s+/g, '') === t);
        if (child && visible(child)) continue;
        iconEls++; if (iconSamples.length < 12) iconSamples.push(t);
      }
    }
    for (const h of body.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
      if (!visible(h)) continue;
      const g = graphemes(collapse(h.textContent));
      if (g.length > 2 && isEmoji(g[0]) && g.slice(1).some((x) => /\p{L}/u.test(x))) leadHeadings++;
    }
    out.emoji.iconElements = iconEls;
    out.emoji.leadingHeadings = leadHeadings;
    out.emoji.iconCount = iconEls + leadHeadings;
    out.emoji.samples = iconSamples;
    // inline svg icons
    let svgIcons = 0, svgTotal = 0;
    for (const svg of body.querySelectorAll('svg')) {
      if (svg.parentElement && svg.parentElement.closest('svg')) continue;
      if (!visible(svg)) continue;
      svgTotal++;
      const r = svg.getBoundingClientRect();
      if (r.width <= 64 && r.height <= 64 && r.width >= 8 && r.height >= 8) svgIcons++;
    }
    // icon fonts
    const ICON_FAMILY = /font ?awesome|material (icons|symbols)|bootstrap-icons|remixicon|phosphor|ionicons|icomoon|feather|lucide|tabler/i;
    let iconFont = 0;
    for (const el of all) {
      const tag = el.tagName.toLowerCase();
      if (tag !== 'i' && tag !== 'span') continue;
      if (!visible(el) && !(el.getBoundingClientRect().width > 0)) continue;
      const fam = cs(el).fontFamily;
      const bf = getComputedStyle(el, '::before').fontFamily;
      if (ICON_FAMILY.test(fam) || ICON_FAMILY.test(bf)) iconFont++;
    }
    let imgIcons = 0;
    for (const im of body.querySelectorAll('img')) {
      if (!visible(im)) continue;
      const r = im.getBoundingClientRect();
      if (r.width <= 64 && r.height <= 64) imgIcons++;
    }
    out.icons = { svgIcons, svgTotal, iconFont, imgIcons };
  } catch (e) { notes.push('emoji-error:' + e.message); }

  // ---------- card rows ----------
  try {
    const HEAD = 'h2,h3,h4,h5,h6,[role=heading],strong,b,dt';
    const kids = (el) => {
      const res = [];
      for (const c of el.children) {
        if (cs(c).display === 'contents') res.push(...kids(c));
        else res.push(c);
      }
      return res;
    };
    function cardLike(k) {
      const r = k.getBoundingClientRect();
      if (r.width < 120 || r.height < 60) return false;
      let headEl = k.querySelector(HEAD);
      if (!headEl) {
        // title-like: short bold-ish text
        for (const d of k.querySelectorAll('*')) {
          const s = cs(d); const t = collapse(d.innerText);
          if (t && t.length <= 80 && parseInt(s.fontWeight, 10) >= 600 && parseFloat(s.fontSize) >= 15 && d.children.length === 0) { headEl = d; break; }
        }
      }
      if (!headEl || !visible(headEl)) return false;
      const total = collapse(k.innerText).length;
      const head = collapse(headEl.innerText).length;
      return total - head >= 20;
    }
    const rows = [];
    for (const el of all) {
      if (el.closest('footer,nav') || !visible(el)) continue;
      const ks = kids(el).filter((k) => visible(k));
      if (ks.length < 2) continue;
      const sorted = ks.map((k) => ({ k, r: k.getBoundingClientRect() })).sort((a, b) => a.r.top - b.r.top || a.r.left - b.r.left);
      const groups = [];
      for (const it of sorted) {
        const g = groups.find((G) => Math.abs(G[0].r.top - it.r.top) <= 12);
        if (g) g.push(it); else groups.push([it]);
      }
      let cardTotal = 0; const local = [];
      for (const g of groups) {
        if (g.length < 2) continue;
        g.sort((a, b) => a.r.left - b.r.left);
        let ok = true;
        for (let i = 1; i < g.length; i++) if (g[i].r.left < g[i - 1].r.right - 2) ok = false;
        const ws = g.map((x) => x.r.width);
        if (Math.max(...ws) / Math.min(...ws) > 1.2) ok = false;
        if (!ok) continue;
        if (!g.every((x) => cardLike(x.k))) continue;
        local.push(g.length);
        cardTotal += g.length;
      }
      if (local.length) {
        let sect = null;
        for (let a = el; a && a !== body; a = a.parentElement) { const h = a.querySelector('h2'); if (h && !el.contains(h)) { sect = collapse(h.innerText).slice(0, 60); break; } if (a.tagName === 'SECTION') { const h2 = a.querySelector('h1,h2,h3'); sect = h2 ? collapse(h2.innerText).slice(0, 60) : null; break; } }
        rows.push({ container: describe(el), rowSizes: local, cardsInContainer: cardTotal, section: sect, inViewport: inViewport(el.getBoundingClientRect()) });
      }
    }
    const sizes = rows.flatMap((r) => r.rowSizes);
    out.cards = {
      rows,
      maxInRow: sizes.length ? Math.max(...sizes) : 0,
      rows3: sizes.filter((n) => n === 3).length,
      rows4: sizes.filter((n) => n === 4).length,
      threeUp: sizes.includes(3),
      totalsFor3up: rows.filter((r) => r.rowSizes.includes(3)).map((r) => r.cardsInContainer),
    };
  } catch (e) { notes.push('cards-error:' + e.message); }

  // ---------- sections ----------
  try {
    const h2s = [...body.querySelectorAll('h2')].filter(visible).map((h) => collapse(h.innerText)).filter(Boolean);
    let labels = h2s, via = 'h2';
    if (!h2s.length) {
      via = 'section-headings';
      labels = [];
      for (const s of body.querySelectorAll('section,[role=region],article')) {
        if (s.parentElement && s.parentElement.closest('section,[role=region],article')) continue;
        const h = s.querySelector('h1,h2,h3,h4');
        if (h && visible(h)) labels.push(collapse(h.innerText));
      }
      notes.push(labels.length ? 'sections:no-h2,used-section-headings' : 'sections:no-h2-no-section-headings');
    }
    const topSections = [...body.querySelectorAll('section')].filter((s) => visible(s) && !(s.parentElement && s.parentElement.closest('section')));
    const footer = [...body.querySelectorAll('footer,[role=contentinfo]')].some(visible);
    out.sections = { labels, via, h2Count: h2s.length, sectionElements: topSections.length, footer };
  } catch (e) { notes.push('sections-error:' + e.message); }

  // ---------- nav / hero image / words ----------
  try {
    const navEl = [...body.querySelectorAll('nav,[role=navigation]')].find(visible);
    let navVia = navEl ? 'nav-element' : null;
    if (!navEl) {
      // top bar with >= 3 links
      for (const el of all) {
        if (!visible(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.top <= 10 && r.height <= 150 && r.width >= VW * 0.8 && el.querySelectorAll('a').length >= 3) { navVia = 'top-bar-with-3+-links'; break; }
      }
    }
    out.nav = { present: !!navVia, via: navVia };
    let heroImg = null;
    for (const el of body.querySelectorAll('img,picture,video,canvas,svg,iframe')) {
      if (el.tagName.toLowerCase() === 'svg' && el.parentElement && el.parentElement.closest('svg')) continue;
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (!inViewport(r) || inNav(el)) continue;
      if (vpArea(r) >= VAREA * 0.03) { heroImg = el.tagName.toLowerCase(); break; }
    }
    if (!heroImg) {
      for (const el of all) {
        const s = cs(el);
        if (!s.backgroundImage.includes('url(')) continue;
        const r = el.getBoundingClientRect();
        if (inViewport(r) && vpArea(r) >= VAREA * 0.03 && visible(el) && !inNav(el)) { heroImg = 'css-background-image'; break; }
      }
    }
    if (!heroImg && bgWinner && bgWinner.kind === 'image') heroImg = 'css-background-image';
    out.heroImage = heroImg;
    const words = (body.innerText || '').split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w));
    out.wordCount = words.length;
    out.docHeight = Math.max(doc.documentElement.scrollHeight, body.scrollHeight);
    out.compatMode = doc.compatMode;
    out.colorSchemeDark = matchMedia('(prefers-color-scheme: dark)').matches;
    out.title = doc.title;
    // Scroll containers: page content that scrolls inside an element, not the window.
    if (out.docHeight <= VH + 2) {
      const sc = all.find((el) => { const s = cs(el); return /(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 50 && el.clientHeight >= VH * 0.8; });
      if (sc) notes.push('full-shot:content-scrolls-inside-' + describe(sc) + ',full.png-shows-viewport-only');
    }
  } catch (e) { notes.push('misc-error:' + e.message); }

  pe.remove();
  return out;
}
