# ai-sameness measurer

Opens each single-file HTML landing page in headless Chromium at 1440x900, takes two screenshots and writes one CSV row plus one JSONL record per page. The tool only measures pages. It does not generate them and it never calls an LLM.

## Run

```
cd /home/research/research-lab/projects/ai-sameness
npm install            # once: playwright-core 1.65.1 + pngjs 7.0.0 (no browser download)
node measure.mjs --pages /data/scratch/ai-sameness/pages \
                 --shots /data/scratch/ai-sameness/shots \
                 --out   /data/scratch/ai-sameness/results.csv
# options: --only id1,id2   --jsonl path (default: <out>.jsonl)   --concurrency 1|2 (default 2, max 2)
```

Each page takes about 3–10 s, including a fresh browser launch per page (a page that hangs takes 2 x 60 s). With 2 in parallel, 275 pages take about 15–25 minutes on an idle machine and longer when other jobs are loading the CPU. Progress goes to stderr.

Test fixtures: `/data/scratch/ai-sameness/fixtures/pages/*.html` (12 hand-written edge cases), run the same way with `--pages` pointed there.

## Runtime environment (affects the numbers; record it with any result)

- Browser: Playwright `chromium_headless_shell-1255` from `~/.cache/ms-playwright`, driven by `playwright-core@1.65.1` (pinned to match that build). The shared libraries come from the existing user-space kit `~/.cache/sheepdog-browser/root` (`LD_LIBRARY_PATH`). Override them with `AISM_LD_LIBRARY_PATH`.
- Fonts: `/data/scratch/ai-sameness/browser-fonts/fonts.conf` (override with `AISM_FONTCONFIG_FILE`). Installed fonts are DejaVu, Liberation (metric clones of Arial/Helvetica/Times/Courier) and Noto Color Emoji, all unpacked from Ubuntu .debs. Generic families map as follows: `sans-serif`, `system-ui` and `ui-sans-serif` → DejaVu Sans or Liberation Sans (Chromium's default sans is Arial, which maps to Liberation Sans); `serif` → DejaVu Serif or Liberation Serif. **There are no Apple or Windows system fonts here.** A page that declares `-apple-system`, `SF Pro`, `Segoe UI` or `Georgia` gets a fallback font, so `*_font_rendered` will differ from what a Mac visitor sees. Web fonts such as Google Fonts do load and render as they would for a visitor.
- Viewport 1440x900, deviceScaleFactor 1. `prefers-color-scheme` is Playwright's default emulation, which is light; the CSV records it in `color_scheme`. Scrollbars are hidden (headless).
- Network is on. Pages load from `file://`.

## Per-page procedure

1. Read the file and write `file_bytes` and `source_notes`.
2. `goto(file://…, waitUntil: load, timeout 15 s)`. On timeout, carry on with whatever rendered (`load_error` is set).
3. Wait up to 4 s for network idle and up to 4 s for `document.fonts.ready`. Each wait that times out adds a note.
4. **Scroll pass**: scroll down in 700 px steps (max 40 steps, 120 ms apart) to the bottom, then back to the top. This fires scroll-reveal and lazy-load code the way a visitor scrolling would.
5. **Settle animations**: every `document.getAnimations()` entry with a finite end gets `finish()`. Infinite ones get `cancel()`, which returns them to their un-animated state (counted in notes).
6. Measure in-page (`lib/inpage.js`) at scrollY = 0, then query rendered fonts over CDP.
7. Screenshots `<id>.viewport.png` (1440x900) and `<id>.full.png` (full page), with Playwright `animations: 'disabled'` and the caret hidden.
8. If the background is a gradient or image, sample it from the viewport screenshot.

Alerts and confirms are dismissed (noted) and popups are closed (noted). **Each page runs in its own Chromium process** (`launchServer`, killed afterwards), so a page that wedges its renderer cannot affect other pages. Every page has a hard limit of 60 s. A page that fails or times out is retried once in a fresh process; a success on retry is noted as `succeeded-on-retry`. A page that fails twice gets a row with `fatal:` in `measure_notes` and `na` in the measured columns, and the batch continues.

## Shared definitions

- **Visible element**: bounding box at least 2x2 px, `display` is not `none`, `visibility` is not hidden, and the product of `opacity` up the ancestor chain is at least 0.05.
- **First viewport**: the rectangle (0,0)–(1440,900) at scrollY = 0. An element is "in the first viewport" if its box intersects that rectangle.
- **Button-like element**: a visible `a`, `button`, `[role=button]` or `input[type=submit|button]` that meets all of these:
  - box between 16x16 and 600x120 px
  - **and** either a background (colour alpha × opacity ≥ 0.1, or a gradient background) or a border on all four sides (≥ 1 px, colour alpha ≥ 0.3)
  - **and** horizontal padding ≥ 12 px total, or vertical padding ≥ 8 px total, or height ≥ 28 px.

  A plain text link with no background and no border is not button-like. Nested button-likes (an `<a>` wrapping a `<button>`) count once.
- **Nav element**: inside `nav` or `[role=navigation]`; or inside a `header` that is ≤ 200 px tall; or inside a `position: fixed/sticky` ancestor that sits at top ≤ 10 px, is ≤ 200 px tall and is ≥ 50 % of the viewport wide. A tall `<header class="hero">` therefore does not count as nav.
- **Paint-stack resolution at a point (x, y)**: walk `document.elementsFromPoint(x, y)` from top to bottom, with `pointer-events` forced on for the duration so decorative overlays are not skipped. Elements smaller than a minimum area are skipped (see each use).
  - An element (or a full-cover `::before`/`::after`) with a `url()` background, or an `img`, `video`, `canvas`, `iframe` or `picture` (or an `svg` covering ≥ 10 % of the viewport), stops the walk as **image**.
  - A gradient with all stops opaque stops it as **gradient**. A gradient with any translucent stop is remembered and the walk continues: if an image is found underneath, the result is **image** (note `bg:image-under-translucent-gradient`), otherwise **gradient**.
  - A background colour with alpha × opacity ≥ 0.95 stops it as a **solid** colour. Translucent colours are alpha-composited over whatever is found below.
  - If nothing stops the walk, the root canvas applies: `html`'s background, or `body`'s if `html` is transparent (CSS background propagation), or white if both are transparent (**browser-default**).
- **Colours** are read from computed style. Any CSS colour syntax (oklch, lab, color-mix …) is converted to sRGB hex by painting it on a 1x1 canvas.

## Columns

| column | definition |
|---|---|
| `id`, `model`, `prompt`, `run` | File name without `.html`, split on `__` into model, prompt and run. The leading `r` is stripped from run. If the id does not have 3 parts, all three are `na` and a note is added. |
| `file_bytes` | Size of the file on disk. |
| `source_notes` | Static checks on the source text: `empty-file`, `whitespace-only`, `markdown-fence-in-source` (file starts or ends with ```` ``` ````; the page is still rendered as-is, so the fence text shows on the page), `no-<html>-tag`, `no-doctype`, `no-closing-</html>-at-end(truncated?)`. |
| `color_scheme` | `matchMedia('(prefers-color-scheme: dark)')` inside the page: `light` or `dark`. Always `light` under the default setup. |
| `compat_mode` | `CSS1Compat` (standards mode) or `BackCompat` (quirks mode, e.g. fence text before the doctype; noted). |
| `bg_color` | Effective page background. Paint-stack resolution runs at a 10x8 grid of points (x = 5 %, 15 % … 95 % of width; y = 3 % … 97 % of height, 8 rows). Elements whose visible area in the viewport is < 30 % of the viewport are skipped, so nav bars, cards and buttons are looked through. The result that covers the most grid points wins. A **solid** winner gives its composited hex. A **gradient** or **image** winner gives the dominant colour of the winning element's box (clipped to the viewport) in the viewport screenshot: pixels on a 2 px grid are quantised to 4 bits per channel, the most populated bin is taken and the mean of its pixels is reported. |
| `bg_source` | `css-solid`, `gradient` (sampled), `image` (sampled), `browser-default` (no CSS background anywhere, so white is what the browser paints; noted), `sampled` (DOM resolution failed, so the whole viewport was sampled; noted) or `na`. |
| `bg_coverage` | Share of the 80 grid points that the winning background covers (0–1). Low values mean a split first viewport; the JSONL has the runners-up. |
| `bg_hue_bucket`, `accent_hue_bucket` | See *Hue buckets* below. |
| `bg_hue_family`, `accent_hue_family` | The pure hue range (table below), ignoring lightness: `neutral` if HSL S < 0.12, L ≤ 0.08 or L ≥ 0.99. Example: `#0f172a` is bucket `dark-navy`, family `blue`. |
| `bg_lightness` | CIE L\*/100 of `bg_color` (perceived lightness, 0–1). |
| `bg_luminance` | WCAG relative luminance Y of `bg_color` (0–1). |
| `theme` | `light` if `bg_lightness` ≥ 0.5, else `dark`. L\* = 50 corresponds to Y ≈ 0.18. |
| `accent_color` | Background colour of the primary button. Candidates are button-like elements intersecting the first viewport that are **filled**: either a gradient background, or background alpha × opacity ≥ 0.5 **and** an RGB distance ≥ 40 from the colour behind the button (paint-stack resolution at the button centre, excluding the button; if what is behind is an image or gradient, any opaque fill counts). Non-nav filled buttons are preferred (`hero-button`), then nav filled buttons (`nav-button`). Within a group the largest area wins; near-ties (within 2 %) go to the first in DOM order. A translucent fill is composited over its backdrop. A gradient button reports its **first colour stop** and sets `accent_gradient=y`; if the first stop is transparent, the first opaque stop is used and noted. **Fallback** when no filled button exists: the most common chromatic (HSL S ≥ 0.12, 0.08 < L < 0.95) text colour among visible non-button `a`, `h1`, `h2`, `h3` elements (`link-colour-fallback`). If that finds nothing too: `na` with source `none`. |
| `accent_source` | `hero-button`, `nav-button`, `link-colour-fallback` or `none`. "hero" here means "in the first viewport and not in nav". |
| `accent_gradient` | `y` if the chosen button's background is a gradient. |
| `gradient_present`, `gradient_where` | `y` if any visible element (or its `::before`/`::after`) intersecting the first viewport, or `html`/`body`, has a `linear-`, `radial-` or `conic-gradient` (including `repeating-`) in computed `background-image` and is at least 16x16 px. `where` (multi-valued, `;`-joined): `text` (`background-clip: text`), `button` (inside a button-like element), `hero-bg` (`html`/`body`, the background winner, or covering ≥ 25 % of the viewport), `other` (badges, cards, blobs, dividers). Gradients drawn inside SVG `<linearGradient>` or on canvas are **not** counted. |
| `gradient_fullpage`, `gradient_fullpage_where` | The same test over the whole page. |
| `h1_count` | Number of `h1` elements in the DOM, visible or not. |
| `h1_text` | Whitespace-collapsed `innerText` of the first visible `h1`. `na` when there is none (note `h1:none` or `h1:present-but-not-visible`). |
| `h1_in_viewport` | The first visible `h1` intersects the first viewport. |
| `h1_centered` | `y` iff the h1 is in the first viewport **and** every rendered line of its text (a Range over its contents, fragments merged per line) has a midpoint within 4 % of the viewport width (57.6 px) of x = 720, **and** either computed `text-align` is `center`, or the text's leftmost edge is ≥ 10 % of the viewport width from the left (a shrink-wrapped, margin-centred block). *This is stricter than "text-align: center"*: centred text inside a left column of a split hero is `n` (noted `h1:text-align-center-but-not-centred-on-page`). Use `h1_text_align` for the looser reading. |
| `h1_text_align` | Computed `text-align` of the h1 (`start`, `left`, `center` …). |
| `h1_font_declared`, `body_font_declared` | First family in the computed `font-family` stack, quotes stripped. This can be a generic family (`sans-serif`, `system-ui`, `ui-sans-serif`). |
| `h1_font_rendered`, `body_font_rendered` | The platform font that actually drew the most glyphs, from CDP `CSS.getPlatformFontsForNode` summed over the element and its descendants. Style suffixes (`SemiBold`, `ExtraLight`, `Italic`, `Variable` …) are stripped. Google variable fonts report their default instance name, e.g. "Source Sans 3 ExtraLight", which is cleaned to "Source Sans 3". The raw list with glyph counts is in the JSONL (`h1_fonts`, `body_fonts`). **Body element** = the first visible `<p>` with ≥ 40 characters that is not inside nav, footer, a link, a button or a heading; else the first visible `p/li/span/div/td/dd/blockquote` with ≥ 40 characters of its own text (noted); else `na`. See the environment note: system-font stacks render as DejaVu or Liberation here. |
| `external_fonts` | Font families loaded from outside the file. Sources: `family=` parameters of Google Fonts or Bunny Fonts URLs in `<link>` and `@import`; `@font-face` rules with a remote `url(http…)` in readable stylesheets; and, when a font file was fetched over the network and some stylesheet was cross-origin and unreadable, every family in `document.fonts` with status `loaded` that is not defined by a `data:` URI (this is how Font Awesome on cdnjs shows up). Fontshare and Typekit family names are not parsed (noted). |
| `external_font_hosts` | Hosts that served a request of type `font`, plus known font-CSS hosts (fonts.googleapis.com, fonts.bunny.net, api.fontshare.com, use.typekit.net, rsms.me …) seen in requests or links. |
| `buttons_in_viewport` | Count of button-like elements intersecting the first viewport. `_nav` and `_nonnav` split it by the nav rule. |
| `primary_button_text` | Collapsed text (or `value`, or `aria-label`) of the chosen primary button. `na` when the accent came from the fallback. |
| `three_card_grid` | `y` if the page has at least one **3-up card row**, excluding anything inside `footer` or `nav`. A card row is found inside any container: its visible children (looking through `display: contents`) are grouped into rows by top edge (±12 px). A row of ≥ 2 children counts only if the children do not overlap horizontally, max width / min width ≤ 1.2, and **every** child is card-like. Card-like means ≥ 120x60 px, containing a heading (`h2`–`h6`, `[role=heading]`, `strong`, `b`, `dt`, or a leaf element with weight ≥ 600, size ≥ 15 px and ≤ 80 characters) and ≥ 20 characters of text outside that heading. Pricing tiers and testimonial triples that fit the shape count too (the JSONL lists each row's container and nearest section heading). |
| `max_cards_in_row` | Largest card row found (0 if none). |
| `rows_3up`, `rows_4up` | Number of card rows with exactly 3 and exactly 4 cards. A 6-card grid at 3 per row gives `rows_3up=2`. |
| `grid_cards_total` | For each container that has a 3-up row, the total cards in its card rows (`;`-joined; `0` if none). 6-card 3x2 grid → `6`. |
| `emoji_icon_count` | Emoji used as icons: (a) visible elements whose whole text is 1–2 emoji graphemes (only the innermost such element counts), plus (b) visible headings whose text starts with an emoji followed by words. |
| `emoji_total` | Emoji graphemes in `body.innerText`. A grapheme is an emoji if it contains an `Emoji_Presentation` code point, or an `Extended_Pictographic` one that has VS16 (U+FE0F) or is ≥ U+1F000. So ©, ®, ™, ✓ and ★ are not counted; ✅, ⚡ and ✨ are. |
| `svg_icon_count` | Visible outermost inline `<svg>` sized 8–64 px in both dimensions. `svg_total` = all visible outermost inline svgs. |
| `icon_font_count` | Visible `<i>`/`<span>` elements whose own or `::before` font-family matches Font Awesome, Material Icons/Symbols, bootstrap-icons, remixicon, Phosphor, Ionicons, icomoon, feather, lucide or tabler. |
| `img_icon_count` | Visible `<img>` ≤ 64x64 px. |
| `section_order` | Collapsed texts of visible `h2` elements in document order, joined with ` \| `. If there are no h2s: the first `h1`–`h4` of each outermost `section`, `[role=region]` or `article` (noted). |
| `section_order_norm` | Each label mapped through `SECTION_KEYWORDS` in `lib/tables.mjs` (an ordered regex table; first match wins; unmatched → `other`). Then `hero` is **prepended** if a visible h1 is in the first viewport, and `footer` is **appended** if a visible `footer`/`[role=contentinfo]` exists. These two are structural, not keyword matches. |
| `n_h2` | Visible h2 count. |
| `n_sections` | Visible outermost `<section>` elements. |
| `frameworks` | Labels from the `FRAMEWORKS` regex table in `lib/tables.mjs`, matched against every `<link href>`, `<script src>`, `@import` URL, ES-module import URL in inline scripts, and every URL actually requested. Generic CDN hosts (unpkg, jsdelivr, cdnjs, skypack, esm.sh …) are added as `cdn:<host>`. |
| `external_requests` | Number of http(s) requests made during load, settling and the scroll pass (includes failures). |
| `external_hosts` | The distinct hosts of those requests. |
| `has_external_images` | `y` if any http(s) request was of type `image` (or an image file extension). `external_image_hosts` lists their hosts (e.g. images.unsplash.com, picsum.photos). |
| `failed_requests` | Network failures plus HTTP ≥ 400 responses. |
| `nav_present` | A visible `nav`/`[role=navigation]`, or else a visible element at top ≤ 10 px, ≤ 150 px tall, ≥ 80 % wide, containing ≥ 3 links (noted `nav:no-<nav>,detected-top-bar`). |
| `hero_has_image` | `y` if a visible `img`, `picture`, `video`, `canvas`, outermost `svg` or `iframe` that is not in nav covers ≥ 3 % of the viewport area within the first viewport, or a non-nav element there with a CSS `url()` background covers ≥ 3 %, or the background winner is an image. The kind is in `measure_notes` (`hero-image-kind:…`). Large decorative SVGs count as `svg`. |
| `word_count` | Whitespace tokens of `body.innerText` that contain a letter or digit (visible text only). |
| `load_error` | Navigation error, e.g. the 15 s load timeout. |
| `console_error_count` | Console messages of type `error`. `page_error_count` = uncaught JS exceptions. |
| `measure_notes` | `;`-separated list of every fallback taken and everything that could not be determined. Read it before quoting a number. |
| `viewport_shot`, `full_shot` | Screenshot file names in `--shots`. |

`na` means could not be determined or not applicable (no h1, fatal page). An empty cell in a list column means an empty list.

### Hue buckets (`lib/colour.mjs`)

HSL is computed from the hex (h in degrees; s, l in 0–1), with chroma c = max − min (0–1). The first matching rule wins:

1. `black`: L ≤ 0.08
2. `white`: L ≥ 0.95 and c ≤ 0.06 (includes barely tinted whites such as #fdf2f8 and #f8fafc; the tint shows in `_hue_family`)
3. `cream/beige`: L ≥ 0.80, hue in [20°, 65°), 0.03 < c ≤ 0.16
4. `gray`: S < 0.12
5. `dark-navy`: L ≤ 0.22, hue in [195°, 235°)
6. `dark-tinted`: L ≤ 0.22, any other hue (e.g. #1e1b4b; its family is still `purple/indigo`)
7. Hue ranges:

| bucket | hue range |
|---|---|
| red | [345°, 360°) ∪ [0°, 15°) |
| orange | [15°, 40°) |
| yellow | [40°, 65°) |
| green | [65°, 165°) |
| teal/cyan | [165°, 200°) |
| blue | [200°, 235°) |
| purple/indigo | [235°, 290°) |
| pink/magenta | [290°, 345°) |

Reference points: Tailwind indigo-600 #4f46e5 = 243° and violet-600 #7c3aed = 262° are purple/indigo; blue-600 #2563eb = 221° is blue; sky-500 #0ea5e9 = 199° is teal/cyan (borderline); fuchsia-600 #c026d3 = 293° is pink/magenta.

For the "AI purple" claim, count `accent_hue_family == purple/indigo` (or the bucket). Dark purple backgrounds land in `dark-tinted`, so use `bg_hue_family` for them.

## JSONL

One object per page with the same id. It holds the raw in-page record (`raw`: background votes and runners-up, button list counts, every gradient in the viewport with its size, card rows with container and section, font sources), `h1_fonts` and `body_fonts` with glyph counts, every request (`url`, `type`), failed requests, console and page errors, notes, timings, and the environment block (tool version, browser build, viewport, colour scheme, timeouts).

## Files

- `measure.mjs`: CLI, browser lifecycle, waits, screenshots, CSV/JSONL.
- `lib/inpage.js`: the in-page measurement (self-contained function).
- `lib/colour.mjs`: HSL, luminance, hue buckets.
- `lib/tables.mjs`: section keyword map, framework table, CDN and font hosts. **Audit these.**
