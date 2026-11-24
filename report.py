#!/usr/bin/env python3
"""Turn the measurer's CSV into the study deliverables (stdlib only, plus ffmpeg and
the repo's headless Chromium for the contact sheets).

  python3 report.py --run /data/scratch/ai-sameness/main \
                    --results /data/scratch/ai-sameness/main/results.csv \
                    --out /data/scratch/ai-sameness/main/out [--no-walls]

Writes to --out: results.csv, summary.md, runlog.md, wall.png, wall_small.jpg,
wall_p5.png, wall_p5_small.jpg (and thumbs/, sheets/ as working files).

Every number in summary.md is computed from the CSV given with --results; the run
logs are only used for runlog.md and for listing planned pages. Planned pages that
have no CSV row are listed as missing, never invented.
"""
import argparse, csv, html, json, math, os, re, statistics, subprocess, sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen  # noqa: E402  (PROMPTS, MODELS: the single source of truth for prompts and model ids)

PROMPTS = gen.PROMPTS
MODELS = gen.MODELS
MODEL_ORDER = list(MODELS)
PROMPT_ORDER = list(PROMPTS)
DISPLAY = {
    "fable-5.2": "Claude Fable 5.2", "opus-5.5": "Claude Opus 5.5", "sonnet-5.5": "Claude Sonnet 5.5",
    "haiku-5.5": "Claude Haiku 5.5", "gpt-6.2-sol": "GPT-6.2 Sol", "gpt-6.2-astra": "GPT-6.2 Astra",
    "gpt-6.1-sol": "GPT-6.1 Sol", "gpt-6.1-terra": "GPT-6.1 Terra", "gpt-6-luna": "GPT-6 Luna",
    "neon": "Gemini 4 Neon", "argon": "Gemini 4 Argon", "hydrogen": "Gemini 4 Hydrogen",
}
PSHORT = {"P1": "project mgmt app", "P2": "Italian restaurant", "P3": "photographer portfolio",
          "P4": "small law firm", "P5": "P1 + \"don't look AI\""}
VENDOR_CLI = {"claude": "Claude Code 2.1.325", "codex": "Codex CLI 0.171.0", "gemini": "Antigravity ACP server 1.3.0"}
REASONING = {"claude": "CLI default (not set)", "gpt-6.2-sol": "Codex catalog default: low",
             "gpt-6.2-astra": "Codex catalog default: low", "gpt-6.1-sol": "Codex catalog default: low",
             "gpt-6.1-terra": "Codex catalog default: low", "gpt-6-luna": "Codex catalog default: medium",
             "gemini": "High (part of the model id)"}

# ---------------------------------------------------------------- font grouping
# Applied to the *declared* first family (h1_font_declared, body_font_declared), case-folded.
SYS_SANS = ["-apple-system", "blinkmacsystemfont", "system-ui", "ui-sans-serif", "segoe ui",
            "sf pro", "sf pro display", "sf pro text", "sans-serif"]
SYS_SERIF = ["georgia", "iowan old style", "ui-serif", "serif", "times new roman", "times", "charter",
             "bitstream charter", "palatino", "palatino linotype", "book antiqua", "hoefler text",
             "baskerville", "cambria", "constantia", "didot", "new york"]
SYS_MONO = ["ui-monospace", "sfmono-regular", "sf mono", "menlo", "monaco", "consolas", "monospace",
            "courier new", "courier"]
FONT_GROUP_TEXT = (
    "Declared first font family, case-folded. **system sans** = " + ", ".join(f"`{x}`" for x in SYS_SANS) +
    ". **system serif** = " + ", ".join(f"`{x}`" for x in SYS_SERIF) +
    ". **system mono** = " + ", ".join(f"`{x}`" for x in SYS_MONO) +
    ". Everything else is kept under its own name (so Arial, Helvetica, Helvetica Neue and Roboto stay separate, "
    "and so does any web font such as Inter). The CSV written by this script carries the result in "
    "`h1_font_group` and `body_font_group`.")

STOPWORDS = sorted("a an and are as at be by for from in into is it its it's of on or our so that the their this "
                   "to we with you your".split())

NA = "na"


def json_escaped(path):
    """True if the saved page is HTML still in JSON string form (literal \\n and \\" instead of newlines and
    quotes), which happens when the captured HTML came from inside a written-out tool call. Such a page renders
    as unstyled text, so its measurements describe a broken render, not the page the model wrote."""
    try:
        t = Path(path).read_text(errors="replace")
    except OSError:
        return None
    lit = t.count("\\n")
    return lit > 20 and lit > t.count("\n")


def font_group(v):
    if v in (None, "", NA):
        return NA
    k = v.strip().strip("'\"").casefold()
    if k in SYS_SANS:
        return "system sans"
    if k in SYS_SERIF:
        return "system serif"
    if k in SYS_MONO:
        return "system mono"
    return k  # display name restored later (most common original spelling)


# ---------------------------------------------------------------- small helpers
def kofn(k, n, pct=False):
    if pct and n:
        return f"{k} of {n} ({100 * k / n:.0f}%)"
    return f"{k} of {n}"


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def median(xs):
    return statistics.median(xs) if xs else None


def fmt_num(x):
    if x is None:
        return "–"
    return f"{x:g}" if isinstance(x, float) else str(x)


def fisher_two_sided(a, b, c, d):
    """Two-sided Fisher exact p for [[a, b], [c, d]] (sum of tables at most as likely as observed)."""
    r1, r2, c1 = a + b, c + d, a + c
    n = r1 + r2

    def lp(x):
        return (math.lgamma(r1 + 1) - math.lgamma(x + 1) - math.lgamma(r1 - x + 1) +
                math.lgamma(r2 + 1) - math.lgamma(c1 - x + 1) - math.lgamma(r2 - c1 + x + 1) -
                (math.lgamma(n + 1) - math.lgamma(c1 + 1) - math.lgamma(n - c1 + 1)))
    lo, hi = max(0, c1 - r2), min(r1, c1)
    pobs = lp(a)
    p = sum(math.exp(lp(x)) for x in range(lo, hi + 1) if lp(x) <= pobs + 1e-9)
    return min(1.0, p)


def scrub(s, n=160):
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", s)
    return (s[:n] + "…") if len(s) > n else s


def words(text):
    return re.findall(r"[0-9a-z]+(?:['’][a-z]+)?", text.casefold())


def norm_text(t):
    return re.sub(r"\s+", " ", t or "").strip().casefold()


def loose_text(t):
    return " ".join(re.findall(r"[0-9a-z]+", (t or "").casefold()))


# ---------------------------------------------------------------- derived per-page values
def yn(v):
    return v if v in ("y", "n") else NA


def derive(r):
    """Per-page categorical values used by the summary. Every one is a pure function of CSV columns."""
    d = {}
    d["bg_hue_bucket"] = r.get("bg_hue_bucket") or NA
    d["accent_hue_bucket"] = r.get("accent_hue_bucket") or NA
    d["gradient_present"] = yn(r.get("gradient_present"))
    d["theme"] = r.get("theme") if r.get("theme") in ("light", "dark") else NA
    d["h1_font_group"] = r["h1_font_group"]
    d["body_font_group"] = r["body_font_group"]
    ef = r.get("external_fonts", "")
    d["external_fonts_any"] = NA if ef == NA else ("y" if ef.strip() else "n")
    d["h1_centered"] = yn(r.get("h1_centered"))
    b = r.get("buttons_in_viewport", NA)
    d["buttons_in_viewport"] = b if re.fullmatch(r"\d+", b or "") else NA
    d["three_card_grid"] = yn(r.get("three_card_grid"))
    d["three_up_any"] = yn(r.get("three_up_any"))
    e = r.get("emoji_icon_count", NA)
    d["emoji_icon_any"] = ("y" if int(e) > 0 else "n") if re.fullmatch(r"\d+", e or "") else NA
    fw = r.get("frameworks", "")
    d["framework_any"] = NA if fw == NA else ("y" if fw.strip() else "n")
    so = r.get("section_order_norm", NA)
    d["section_order_norm"] = so if so and so != NA else NA
    return d


# Measures used for traits and for the agreement score (fixed list, in this order).
TRAIT_MEASURES = [
    ("bg_hue_bucket", "background hue bucket"),
    ("accent_hue_bucket", "accent (primary button) hue bucket"),
    ("gradient_present", "gradient in first viewport"),
    ("theme", "theme"),
    ("h1_font_group", "h1 font (declared, grouped)"),
    ("body_font_group", "body font (declared, grouped)"),
    ("external_fonts_any", "external fonts loaded"),
    ("h1_centered", "h1 centred"),
    ("buttons_in_viewport", "buttons in first viewport"),
    ("three_card_grid", "three-card feature grid"),
    ("emoji_icon_any", "emoji used as icons"),
    ("framework_any", "any CSS framework/CDN/font or icon service"),
    ("section_order_norm", "section order (normalised, whole sequence)"),
]
TRAIT_LABEL = dict(TRAIT_MEASURES)


# ---------------------------------------------------------------- loading
def load_jsonl(p):
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def load(args):
    run = Path(args.run)
    jobs = load_jsonl(run / "jobs.jsonl")
    planned = {j["id"]: j for j in jobs}
    with open(args.results, newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        rows_all = list(rd)
    ids = Counter(r["id"] for r in rows_all)
    dup = [i for i, c in ids.items() if c > 1]
    if dup:
        sys.exit(f"duplicate ids in {args.results}: {dup}")
    other_arm = [r["id"] for r in rows_all if r.get("arm", "default") not in ("default", "")]
    excl = set()
    for x in (args.exclude or "").split(","):
        x = x.strip()
        if x.startswith("@"):
            excl |= {l.split("#")[0].strip() for l in Path(x[1:]).read_text().splitlines() if l.split("#")[0].strip()}
        elif x:
            excl.add(x)
    unknown_excl = sorted(excl - ids.keys())
    if unknown_excl:
        sys.exit(f"--exclude ids not in the CSV: {unknown_excl}")
    excluded = sorted(excl)
    rows = [r for r in rows_all if r.get("arm", "default") in ("default", "") and r["id"] not in excl]
    unknown_model = [r["id"] for r in rows if r.get("model") not in MODELS]
    if unknown_model:
        sys.exit(f"rows with a model not in gen.MODELS: {unknown_model}")
    for r in rows:
        r["vendor"], r["model_id"] = MODELS[r["model"]]
        r["h1_font_group"] = font_group(r.get("h1_font_declared"))
        r["body_font_group"] = font_group(r.get("body_font_declared"))
    # display spelling for non-system font keys: most common original spelling
    spell = defaultdict(Counter)
    for r in rows:
        for col, g in (("h1_font_declared", "h1_font_group"), ("body_font_declared", "body_font_group")):
            if r[g] not in (NA, "system sans", "system serif", "system mono"):
                spell[r[g]][r[col].strip().strip("'\"")] += 1
    for r in rows:
        for g in ("h1_font_group", "body_font_group"):
            if r[g] in spell:
                r[g] = spell[r[g]].most_common(1)[0][0]
        r["_d"] = derive(r)
    pages_dir = run / "pages"
    page_files = {p.stem for p in pages_dir.glob("*.html")} if pages_dir.exists() else set()
    for r in rows:
        e = json_escaped(pages_dir / f"{r['id']}.html")
        r["page_json_escaped"] = NA if e is None else ("y" if e else "n")
    escaped_all = sorted(p.stem for p in pages_dir.glob("*.html") if json_escaped(p)) if pages_dir.exists() else []
    return run, jobs, planned, cols, rows, other_arm, page_files, excluded, escaped_all


# ---------------------------------------------------------------- tables
def groups(rows):
    """[(label, rows)] for All, each prompt, each model."""
    g = [("**All pages**", rows)]
    for p in PROMPT_ORDER:
        g.append((f"{p} {PSHORT[p]}", [r for r in rows if r["prompt"] == p]))
    for m in MODEL_ORDER:
        g.append((DISPLAY[m], [r for r in rows if r["model"] == m]))
    return g


def order_values(counter):
    vals = [v for v in counter if v != NA]
    vals.sort(key=lambda v: (-counter[v], v))
    if NA in counter:
        vals.append(NA)
    return vals


def cat_table(rows, key, max_cols=None, value_order=None):
    """One table: rows = All / prompts / models, columns = values, cells 'k of n'."""
    overall = Counter(r["_d"][key] if key in r["_d"] else r[key] for r in rows)
    vals = value_order or order_values(overall)
    shown, other = vals, []
    if max_cols and len(vals) > max_cols:
        shown = [v for v in vals if v != NA][:max_cols]
        other = [v for v in vals if v not in shown and v != NA]
        if NA in vals:
            shown = shown + [NA]
    header = ["", "n"] + [f"`{v}`" for v in shown] + ([f"other ({len(other)} names)"] if other else [])
    out = []
    for label, rs in groups(rows):
        n = len(rs)
        c = Counter(r["_d"][key] if key in r["_d"] else r[key] for r in rs)
        pct = label.startswith("**All")
        cells = [kofn(c[v], n, pct) for v in shown]
        if other:
            cells.append(kofn(sum(c[v] for v in other), n, pct))
        out.append([label, n] + cells)
    return md_table(header, out), vals, other, overall


def yes_table(rows, keys_labels):
    """Several y/n measures side by side: cells 'k of n' (count of y); na shown if any."""
    header = ["", "n"] + [lab for _, lab in keys_labels]
    out = []
    for label, rs in groups(rows):
        n = len(rs)
        pct = label.startswith("**All")
        cells = []
        for k, _ in keys_labels:
            y = sum(1 for r in rs if r["_d"][k] == "y")
            na = sum(1 for r in rs if r["_d"][k] == NA)
            cells.append(kofn(y, n, pct) + (f" ({na} na)" if na else ""))
        out.append([label, n] + cells)
    return md_table(header, out)


def multi_table(rows, split_fn, max_cols=None, none_label="(none)"):
    """Multi-valued per page (frameworks, section types): pages containing each value."""
    overall = Counter()
    for r in rows:
        vs = split_fn(r)
        for v in (set(vs) if vs else {none_label}):
            overall[v] += 1
    vals = order_values(overall)
    if max_cols:
        vals = vals[:max_cols]
    header = ["", "n"] + [f"`{v}`" for v in vals]
    out = []
    for label, rs in groups(rows):
        n = len(rs)
        pct = label.startswith("**All")
        c = Counter()
        for r in rs:
            vs = split_fn(r)
            for v in (set(vs) if vs else {none_label}):
                c[v] += 1
        out.append([label, n] + [kofn(c[v], n, pct) for v in vals])
    return md_table(header, out), overall


def fw_split(r):
    fw = r.get("frameworks", "")
    if fw == NA:
        return [NA]
    return [x.strip() for x in fw.split(";") if x.strip()]


def sec_split(r):
    so = r.get("section_order_norm", NA)
    if not so or so == NA:
        return [NA]
    return [x.strip() for x in so.split("|") if x.strip()]


def ext_font_split(r):
    ef = r.get("external_fonts", "")
    if ef == NA:
        return [NA]
    return [x.strip() for x in ef.split(";") if x.strip()]


def num(r, col):
    v = r.get(col, NA)
    try:
        return float(v) if "." in v else int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- summary
def section_run(rows, planned, page_files, other_arm, results_path, cols, excluded):
    L = []
    have = {r["id"] for r in rows} | set(excluded)
    planned_ids = list(planned)
    pages_exist = [i for i in planned_ids if i in page_files]
    not_generated = sorted(i for i in planned_ids if i not in page_files)
    not_measured = sorted(i for i in planned_ids if i in page_files and i not in have)
    not_planned = sorted(i for i in have if i not in planned)
    fatal = sorted(r["id"] for r in rows if "fatal:" in (r.get("measure_notes") or ""))
    runs = sorted({j["run"] for j in planned.values()})
    L.append("## What was run\n")
    L.append(f"- Models (12): " + "; ".join(f"{DISPLAY[m]} (`{MODELS[m][1]}`)" for m in MODEL_ORDER) +
             ". Claude models via Claude Code, GPT models via Codex CLI, Gemini models via the Antigravity ACP server; "
             "each CLI's default instructions; one fresh session per page (see runlog.md).")
    L.append("- Prompts, sent verbatim: " + " ".join(f"**{p}** \"{PROMPTS[p]}\"" for p in PROMPT_ORDER))
    L.append(f"- Plan: {len(planned_ids)} pages = 12 models x {len(PROMPTS)} prompts x {len(runs)} runs (n = {len(runs)} per model per prompt). "
             f"P1 run 1 of every model is the pilot page, carried over unchanged.")
    L.append(f"- Pages that exist: {len(pages_exist)} of {len(planned_ids)}. Rows in the measurement CSV "
             f"(`{results_path}`): {len(have)}" + (f", of which {len(rows)} are counted ({len(excluded)} left out with --exclude)" if excluded else "") + f". Missing from the CSV: {len(planned_ids) - len(have & set(planned_ids))} "
             f"({len(not_generated)} never generated, {len(not_measured)} generated but not in this CSV).")
    if not_generated:
        L.append(f"  - Not generated: {', '.join(not_generated)}")
    if not_measured:
        L.append(f"  - Generated but not measured in this CSV: {', '.join(not_measured)}")
    if not_planned:
        L.append(f"  - In the CSV but not in the plan (included in the counts): {', '.join(not_planned)}")
    if other_arm:
        L.append(f"  - Rows with arm other than `default` left out of every count: {len(other_arm)} ({', '.join(other_arm)})")
    if fatal:
        L.append(f"  - Measurement failed (row present, measured columns `na`): {', '.join(fatal)}")
    L.append("")
    # cell matrix
    header = ["Model"] + PROMPT_ORDER + ["total"]
    out = []
    for m in MODEL_ORDER:
        cells = []
        for p in PROMPT_ORDER:
            nplan = sum(1 for j in planned.values() if j["model"] == m and j["prompt"] == p)
            nhave = sum(1 for r in rows if r["model"] == m and r["prompt"] == p)
            cells.append(f"{nhave}" if nhave == nplan else f"**{nhave}** of {nplan}")
        cells.append(sum(1 for r in rows if r["model"] == m))
        out.append([DISPLAY[m]] + cells)
    L.append("Measured pages per cell (bold where fewer than planned):\n")
    L.append(md_table(header, out))
    L.append("")
    return L


def section_problems(rows, excluded, escaped_all):
    L = ["## Data problems found by this script\n"]
    esc = [r for r in rows if r["page_json_escaped"] == "y"]
    if esc or escaped_all:
        L.append(f"**Pages saved in JSON-escaped form: {len(escaped_all)} page files** ({', '.join(escaped_all)}). "
                 "The saved file is the page's HTML still written as a JSON string (literal `\\n` and `\\\"` instead "
                 "of line breaks and quotes): the model wrote the page inside a written-out tool call "
                 "(`\"content\": \"<!DOCTYPE html>\\n...`), and the runner's capture took the text as it stood. "
                 "The browser shows these as unstyled text, so their measurements describe a broken render. "
                 f"{len(esc)} of them are counted in this summary" +
                 (" (use `--exclude` to leave them out; the CSV column `page_json_escaped` marks them)" if esc else "") +
                 ". What the measurer recorded for them:\n")
        tab = [[r["id"], r.get("bg_source"), r["_d"]["bg_hue_bucket"], r["_d"]["theme"], r["_d"]["accent_hue_bucket"],
                r["_d"]["gradient_present"], r["_d"]["h1_centered"], r["_d"]["buttons_in_viewport"],
                r["_d"]["three_card_grid"], r["_d"]["h1_font_group"], r["_d"]["body_font_group"]] for r in esc]
        if tab:
            L.append(md_table(["id", "bg_source", "background", "theme", "accent", "gradient", "h1 centred", "buttons",
                               "3-card grid", "h1 font", "body font"], tab))
            L.append("")
    else:
        L.append("- No page files in JSON-escaped form.")
    if excluded:
        L.append(f"**Left out of every count by `--exclude`: {len(excluded)} rows** ({', '.join(excluded)}). "
                 "All n below are without them.\n")
    else:
        L.append("No rows were left out (`--exclude` not used).\n")
    return L


def section_measures(rows):
    L = ["## Measures\n",
         "Every cell is \"k of n\": k pages with that value out of the n pages measured in that row. "
         "Percentages are given only on the All-pages row. `na` = the measurer could not determine it "
         "(definitions: MEASURES.md).\n"]

    def add(title, note, table):
        L.append(f"### {title}\n")
        if note:
            L.append(note + "\n")
        L.append(table)
        L.append("")

    t, *_ = cat_table(rows, "bg_hue_bucket")
    add("Page background hue bucket", "`bg_hue_bucket` (buckets in MEASURES.md; `white` includes barely tinted whites).", t)
    t, *_ = cat_table(rows, "accent_hue_bucket")
    acc_src = Counter(r.get("accent_source", NA) for r in rows)
    add("Accent (primary button) hue bucket",
        "`accent_hue_bucket`. Source of the accent colour over all pages: " +
        ", ".join(f"`{k}` {v}" for k, v in acc_src.most_common()) + ".", t)
    add("Gradient, theme, external fonts, h1 centred (yes counts)",
        "`gradient_present` (any CSS gradient in the first viewport), `theme` = dark, `external_fonts` non-empty, "
        "`h1_centered` (strict: centred on the page, see MEASURES.md).",
        yes_table(rows, [("gradient_present", "gradient"), ("external_fonts_any", "external fonts"),
                         ("h1_centered", "h1 centred")]))
    t, *_ = cat_table(rows, "theme")
    add("Dark / light theme", "`theme` (light if background CIE L* >= 50).", t)

    for col, title in (("h1_font_group", "h1 font (declared, grouped)"), ("body_font_group", "Body font (declared, grouped)")):
        t, vals, other, overall = cat_table(rows, col, max_cols=8)
        raw_col = col.replace("_group", "_declared")
        members = defaultdict(Counter)
        for r in rows:
            members[r["_d"][col]][(r.get(raw_col) or NA).strip()] += 1
        lines = []
        for g in ("system sans", "system serif", "system mono"):
            if g in members:
                lines.append(f"{g} = " + ", ".join(f"`{k}` {v}" for k, v in members[g].most_common()))
        allnames = ", ".join(f"`{v}` {overall[v]}" for v in vals)
        note = (FONT_GROUP_TEXT if col == "h1_font_group" else "Same grouping as the h1 font.") + \
            "\n\nWhat went into each system group here: " + ("; ".join(lines) or "none") + \
            f".\n\nAll values over all pages: {allnames}."
        add(title, note, t)

    t, overall = multi_table(rows, ext_font_split, max_cols=10)
    add("External font families loaded (top 10 families)",
        "Pages that load each family (from `external_fonts`); a page can load several. All families: " +
        ", ".join(f"`{k}` {v}" for k, v in overall.most_common()) + ".", t)

    # buttons
    vals = sorted({int(r["_d"]["buttons_in_viewport"]) for r in rows if r["_d"]["buttons_in_viewport"] != NA})
    header = ["", "n"] + [str(v) for v in vals] + (["na"] if any(r["_d"]["buttons_in_viewport"] == NA for r in rows) else []) + ["median"]
    out = []
    for label, rs in groups(rows):
        n = len(rs)
        c = Counter(r["_d"]["buttons_in_viewport"] for r in rs)
        xs = [int(r["_d"]["buttons_in_viewport"]) for r in rs if r["_d"]["buttons_in_viewport"] != NA]
        pct = label.startswith("**All")
        cells = [kofn(c[str(v)], n, pct) for v in vals]
        if "na" in header:
            cells.append(kofn(c[NA], n))
        out.append([label, n] + cells + [fmt_num(median(xs))])
    add("Buttons in the first viewport",
        "`buttons_in_viewport` (button-like elements, excluding those inside a product mock-up and announcement pills). "
        "Columns are the count of buttons; cells are pages.", md_table(header, out))

    add("Three-card feature grid",
        "`three_card_grid` = a row of exactly 3 **feature** cards (strict); `three_up_any` = any row of exactly 3 cards "
        "of any kind (pricing tiers, steps, testimonials, stats, mock-up columns). The gap between the two columns is "
        "rows of three that are not feature cards.",
        yes_table(rows, [("three_card_grid", "three_card_grid (feature)"), ("three_up_any", "three_up_any (loose)")]))

    # emoji
    header = ["", "n", "pages with any emoji icon", "emoji icons in total", "max on one page"]
    out = []
    for label, rs in groups(rows):
        xs = [num(r, "emoji_icon_count") for r in rs]
        xs = [x for x in xs if x is not None]
        out.append([label, len(rs), kofn(sum(1 for x in xs if x > 0), len(rs), label.startswith("**All")),
                    sum(xs), max(xs) if xs else "–"])
    add("Emoji used as icons", "`emoji_icon_count` (elements whose whole text is 1-2 emoji, plus headings that start with an emoji).",
        md_table(header, out))

    # texts
    L.append("### Most common h1 texts and primary button texts\n")
    for col, title in (("h1_text", "h1"), ("primary_button_text", "Primary button")):
        have = [r for r in rows if r.get(col) not in (None, "", NA)]
        exact = Counter(norm_text(r[col]) for r in have)
        loose = Counter(loose_text(r[col]) for r in have)
        L.append(f"**{title}** (`{col}`): present on {kofn(len(have), len(rows))} pages. "
                 f"Distinct texts after case-folding and whitespace collapse: {len(exact)}. "
                 f"Texts shared by 2 or more pages: {sum(1 for v in exact.values() if v > 1)} "
                 f"(covering {sum(v for v in exact.values() if v > 1)} pages). "
                 f"Looser match (case-folded, letters and digits only, so punctuation and arrows are ignored): "
                 f"{len(loose)} distinct, {sum(1 for v in loose.values() if v > 1)} shared by 2+ pages "
                 f"(covering {sum(v for v in loose.values() if v > 1)} pages).\n")
        dups = [(t, c) for t, c in exact.most_common() if c > 1]
        if dups:
            tab = []
            for t, c in dups[:20]:
                who = Counter(f"{r['model']} {r['prompt']}" for r in have if norm_text(r[col]) == t)
                tab.append([f"\"{t}\"", c, ", ".join(f"{k}" + (f" x{v}" if v > 1 else "") for k, v in sorted(who.items()))])
            L.append(md_table(["exact text (case-folded)", "pages", "model and prompt"], tab))
            L.append("")
        ldups = [(t, c) for t, c in loose.most_common() if c > 1 and t not in {loose_text(x) for x, _ in dups}]
        if ldups:
            L.append("Additional shared texts under the looser match: " +
                     "; ".join(f"\"{t}\" {c}" for t, c in ldups[:20]) + "\n")
        # top words: pages containing the word
        tab = []
        wc_all = Counter()
        for r in have:
            for w in set(words(r[col])) - set(STOPWORDS):
                wc_all[w] += 1
        tab.append(["all prompts", len(have), ", ".join(f"{w} {c}" for w, c in wc_all.most_common(15))])
        for p in PROMPT_ORDER:
            hp = [r for r in have if r["prompt"] == p]
            wc = Counter()
            for r in hp:
                for w in set(words(r[col])) - set(STOPWORDS):
                    wc[w] += 1
            tab.append([f"{p} {PSHORT[p]}", len(hp), ", ".join(f"{w} {c}" for w, c in wc.most_common(10))])
        L.append(f"Top words in the {title.lower()} text: number of pages whose text contains the word "
                 f"(case-folded; these function words are left out: {' '.join(STOPWORDS)}).\n")
        L.append(md_table(["", "pages with text", "word and pages containing it"], tab))
        L.append("")

    # sections
    L.append("### Section order\n")
    L.append("`section_order_norm`: `hero` is added when an h1 is in the first viewport and `footer` when a footer exists "
             "(structural, not keyword matches); everything between is the label of each h2 section.\n")
    seqs = Counter(r["_d"]["section_order_norm"] for r in rows)
    L.append(f"Distinct sequences: {len([s for s in seqs if s != NA])} over {len(rows)} pages"
             + (f" ({seqs[NA]} na)" if seqs[NA] else "") + ". Most common:\n")
    tab = []
    for s, c in [(s, c) for s, c in seqs.most_common() if s != NA][:12]:
        bym = Counter(r["prompt"] for r in rows if r["_d"]["section_order_norm"] == s)
        tab.append([f"`{s}`", kofn(c, len(rows)), ", ".join(f"{p} {bym[p]}" for p in PROMPT_ORDER if bym[p])])
    L.append(md_table(["sequence", "pages", "by prompt"], tab))
    L.append("")
    L.append("Most common sequence within each prompt:\n")
    tab = []
    for p in PROMPT_ORDER:
        rp = [r for r in rows if r["prompt"] == p]
        sp = Counter(r["_d"]["section_order_norm"] for r in rp if r["_d"]["section_order_norm"] != NA)
        for s, c in sp.most_common(2):
            tab.append([f"{p} {PSHORT[p]}", f"`{s}`", kofn(c, len(rp))])
    L.append(md_table(["prompt", "sequence", "pages"], tab))
    L.append("")
    t, _ = multi_table(rows, sec_split)
    L.append("How often each section type appears at all (pages containing it at least once):\n")
    L.append(t)
    L.append("")

    t, overall = multi_table(rows, fw_split)
    add("CSS framework / CDN / external services",
        "`frameworks` (labels from `lib/tables.mjs`; besides CSS frameworks this includes font services such as "
        "`google-fonts`, icon sets, image services and `cdn:<host>`). A page can have several labels, so a row "
        "need not sum to n. `(none)` = no label at all.", t)

    # file size
    header = ["", "n", "median kB", "min kB", "max kB"]
    out = []
    for label, rs in groups(rows):
        xs = [num(r, "file_bytes") for r in rs]
        xs = [x for x in xs if x is not None]
        out.append([label, len(rs), f"{median(xs) / 1000:.1f}" if xs else "–",
                    f"{min(xs) / 1000:.1f}" if xs else "–", f"{max(xs) / 1000:.1f}" if xs else "–"])
    add("File size", "`file_bytes` of the saved HTML file; kB = 1000 bytes.", md_table(header, out))
    return L


def trait_ranking(rows):
    N = len(rows)
    traits = []
    for key, _ in TRAIT_MEASURES:
        c = Counter(r["_d"][key] for r in rows)
        for v, k in c.items():
            if v == NA:
                continue
            traits.append((key, v, k))
    pos = {k: i for i, (k, _) in enumerate(TRAIT_MEASURES)}
    traits.sort(key=lambda t: (-t[2], pos[t[0]], t[1]))
    return N, traits


def trait_name(key, v):
    return f"{TRAIT_LABEL[key]} = {v}"


def section_traits(rows):
    L = ["## The three most common traits\n"]
    N, traits = trait_ranking(rows)
    L.append("Rule: a trait is one value of one measure from this fixed list: " +
             "; ".join(f"{lab} (`{k}`)" for k, lab in TRAIT_MEASURES) +
             ". Both values of a yes/no measure are traits (\"gradient = n\" is a trait like \"gradient = y\"); "
             "`na` is never a trait. Traits are ranked by the number of pages that have them, out of all "
             f"{N} measured pages (ties: order of the list above, then value). The loose `three_up_any` is left out "
             "because it overlaps the strict feature-grid measure; h1/button texts and file size are not categorical "
             "and are left out. Top 10:\n")
    tab = [[i + 1, trait_name(k, v), kofn(c, N, True)] for i, (k, v, c) in enumerate(traits[:10])]
    L.append(md_table(["rank", "trait", "pages"], tab))
    L.append("")
    top3 = traits[:3]
    pres = [t for t in traits if t[1] != "n"]
    L.append("For comparison, the same ranking with the `n` values left out (so only values that describe something "
             "the page has: `y` answers and categorical values such as a colour bucket, font or theme). This is a second "
             "fixed rule shown alongside, not a replacement:\n")
    L.append(md_table(["rank", "trait", "pages"], [[i + 1, trait_name(k, v), kofn(c, N, True)] for i, (k, v, c) in enumerate(pres[:10])]))
    L.append("")
    L.append("The three most common (first rule): " + "; ".join(f"**{trait_name(k, v)}** ({kofn(c, N, True)})" for k, v, c in top3) + ".")
    L.append("The three most common with `n` values left out (second rule): " + "; ".join(f"**{trait_name(k, v)}** ({kofn(c, N, True)})" for k, v, c in pres[:3]) + ".\n")
    return L, top3, pres[:3]


def agreement(a, b, keys):
    same = tot = 0
    for k in keys:
        x, y = a["_d"][k], b["_d"][k]
        if x == NA or y == NA:
            continue
        tot += 1
        same += x == y
    return same / tot if tot else None


def break_tables(rows, top3, rule):
    L = []
    N = len(rows)
    header = ["Model", "n"] + [trait_name(k, v) for k, v, _ in top3] + ["all three", "none of the three"]
    out = []
    per_model = []
    for m in MODEL_ORDER:
        rs = [r for r in rows if r["model"] == m]
        if not rs:
            continue
        held = [sum(1 for k, v, _ in top3 if r["_d"][k] == v) for r in rs]
        cells = [kofn(sum(1 for r in rs if r["_d"][k] == v), len(rs)) for k, v, _ in top3]
        out.append([DISPLAY[m], len(rs)] + cells + [kofn(held.count(3), len(rs)), kofn(held.count(0), len(rs))])
        per_model.append((sum(held) / (3 * len(rs)), m))
    L.append(f"Share of each model's pages that have each of the three top traits ({rule}):\n")
    L.append(md_table(header, out))
    L.append("")
    for k, v, _ in top3:
        shares = sorted(((sum(1 for r in rows if r["model"] == m and r["_d"][k] == v),
                          sum(1 for r in rows if r["model"] == m), m) for m in MODEL_ORDER
                         if any(r["model"] == m for r in rows)), key=lambda t: (t[0] / t[1], t[2]))
        L.append(f"- Lowest share of \"{trait_name(k, v)}\": " +
                 ", ".join(f"{DISPLAY[m]} {kofn(a, b)}" for a, b, m in shares[:3]) + ".")
    L.append("")
    held = {r["id"]: sum(1 for k, v, _ in top3 if r["_d"][k] == v) for r in rows}
    dist = Counter(held.values())
    L.append("Pages by how many of the three top traits they have: " +
             ", ".join(f"{h} trait{'' if h == 1 else 's'}: {kofn(dist[h], N)}" for h in (3, 2, 1, 0)) + ".\n")
    lo = min(held.values()) if held else 0
    ids = sorted(i for i, h in held.items() if h == lo)
    nas = sorted(r["id"] for r in rows if any(r["_d"][k] == NA for k, _, _ in top3))
    L.append(f"Pages with the fewest ({lo}) of the three: {', '.join(ids)}." +
             (f" (Pages where at least one of the three measures is `na`, counted as not having it: {', '.join(nas)}.)" if nas else "") + "\n")
    return L


def section_break(rows, top3, top3p):
    L = ["## Pages or models that break the pattern\n"]
    L += break_tables(rows, top3, "first rule")
    L += break_tables(rows, top3p, "second rule, `n` values left out")

    # agreement
    keys = [k for k, _ in TRAIT_MEASURES]
    L.append("### Agreement between pages (descriptive)\n")
    L.append(f"For two pages, agreement = the share of the {len(keys)} trait measures listed above on which they have the "
             "same value (measures that are `na` on either page are skipped); the numbers below are means over all pairs of "
             "pages of each kind. It describes how alike the measured values are, nothing more: it is not a test, the "
             "measures are not independent, and a different measure list would give different numbers.\n")
    pairs = defaultdict(list)
    for a, b in combinations(rows, 2):
        s = agreement(a, b, keys)
        if s is None:
            continue
        sm, sp = a["model"] == b["model"], a["prompt"] == b["prompt"]
        pairs[(sm, sp)].append(s)
        if sm:
            pairs[("model", a["model"], sp)].append(s)
        elif sp:
            pairs[("other", a["model"], True)].append(s)
            pairs[("other", b["model"], True)].append(s)
    def mean(xs):
        return f"{statistics.mean(xs):.2f} ({len(xs)} pairs)" if xs else "–"
    tab = [["same model, same prompt (the repeated runs)", mean(pairs[(True, True)])],
           ["same model, different prompt", mean(pairs[(True, False)])],
           ["different model, same prompt", mean(pairs[(False, True)])],
           ["different model, different prompt", mean(pairs[(False, False)])],
           ["same prompt (any model)", mean(pairs[(True, True)] + pairs[(False, True)])],
           ["different prompt (any model)", mean(pairs[(True, False)] + pairs[(False, False)])],
           ["same model (any prompt)", mean(pairs[(True, True)] + pairs[(True, False)])],
           ["different model (any prompt)", mean(pairs[(False, True)] + pairs[(False, False)])]]
    L.append(md_table(["pairs of pages", "mean agreement"], tab))
    L.append("")
    tab = []
    for m in MODEL_ORDER:
        if not any(r["model"] == m for r in rows):
            continue
        tab.append([DISPLAY[m], mean(pairs[("model", m, True)]), mean(pairs[("model", m, False)]),
                    mean(pairs[("other", m, True)])])
    L.append("Per model: agreement among its own runs on the same prompt, among its own pages across prompts, and "
             "between its pages and other models' pages on the same prompt. A lower first column means the model's "
             "repeated runs differ more from each other.\n")
    L.append(md_table(["Model", "own runs, same prompt", "own pages, different prompt", "vs other models, same prompt"], tab))
    L.append("")
    return L


def section_p5(rows):
    L = ["## How P5 differs from P1\n",
         "P5 is P1 with \"Don't make it look AI-generated.\" appended. Same models, 5 runs each. Per-model cells are "
         "\"k of n\" (n = 5 unless pages are missing). The pooled Fisher exact p (two-sided) compares all P1 pages with "
         "all P5 pages and is given only as a rough guide to what 5-run noise can produce. Labels use a fixed rule: "
         "p < 0.01 \"larger than run-to-run noise usually gives\"; 0.01 <= p < 0.05 \"borderline\"; otherwise "
         "\"within run-to-run noise\". Many comparisons are made below (the count is at the end), so about 1 in 20 of "
         "them will reach p < 0.05 by chance alone.\n"]
    p1 = [r for r in rows if r["prompt"] == "P1"]
    p5 = [r for r in rows if r["prompt"] == "P5"]
    tests = []

    def verdict(p):
        return ("larger than run-to-run noise usually gives" if p < 0.01 else
                "borderline" if p < 0.05 else "within run-to-run noise")

    # yes/no measures
    yn_meas = [("gradient_present", "y", "gradient in first viewport"), ("theme", "dark", "dark theme"),
               ("external_fonts_any", "y", "external fonts loaded"), ("h1_centered", "y", "h1 centred"),
               ("three_card_grid", "y", "three-card feature grid"), ("three_up_any", "y", "any row of three cards"),
               ("emoji_icon_any", "y", "emoji used as icons"), ("framework_any", "y", "any framework/CDN/service")]
    header = ["Model"] + [f"{lab}: P1 / P5" for _, _, lab in yn_meas]
    out = []
    for m in MODEL_ORDER:
        a = [r for r in p1 if r["model"] == m]
        b = [r for r in p5 if r["model"] == m]
        if not a and not b:
            continue
        out.append([DISPLAY[m]] + [f"{sum(r['_d'][k] == v for r in a)} of {len(a)} / {sum(r['_d'][k] == v for r in b)} of {len(b)}"
                                   for k, v, _ in yn_meas])
    pooled = []
    summary_rows = []
    complete = [m for m in MODEL_ORDER if sum(r["model"] == m for r in p1) == 5 and sum(r["model"] == m for r in p5) == 5]
    c1 = [r for r in p1 if r["model"] in complete]
    c5 = [r for r in p5 if r["model"] in complete]
    for k, v, lab in yn_meas:
        a1 = sum(r["_d"][k] == v for r in p1)
        a5 = sum(r["_d"][k] == v for r in p5)
        p = fisher_two_sided(a1, len(p1) - a1, a5, len(p5) - a5)
        tests.append(p)
        b1 = sum(r["_d"][k] == v for r in c1)
        b5 = sum(r["_d"][k] == v for r in c5)
        pooled.append(f"**{a1} of {len(p1)} / {a5} of {len(p5)}**")
        up = dn = 0
        for m in MODEL_ORDER:
            x = sum(r["_d"][k] == v for r in p1 if r["model"] == m)
            y = sum(r["_d"][k] == v for r in p5 if r["model"] == m)
            up += y > x
            dn += y < x
        row = [lab, kofn(a1, len(p1), True), kofn(a5, len(p5), True), f"{p:.3g}", verdict(p),
               f"{up} up, {dn} down, {12 - up - dn} same"]
        if len(complete) < len(MODEL_ORDER):
            row += [f"{b1} of {len(c1)} / {b5} of {len(c5)}"]
        summary_rows.append(row)
    out.append(["**All (pooled)**"] + pooled)
    L.append("### Yes/no measures\n")
    hdr = ["measure", "P1", "P5", "Fisher p (pooled)", "label", "models where P5 count is higher / lower / equal"]
    if len(complete) < len(MODEL_ORDER):
        hdr.append(f"P1 / P5, only the {len(complete)} models with all 5 + 5 pages")
        L.append(f"Not every model has 5 pages for both prompts here, so the pooled P1 and P5 groups mix the models in "
                 f"different proportions. The last column repeats the counts for the {len(complete)} models that have "
                 f"all 10 pages ({', '.join(DISPLAY[m] for m in complete)}).\n")
    L.append(md_table(hdr, summary_rows))
    L.append("\nPer model (P1 / P5):\n")
    L.append(md_table(header, out))
    L.append("")

    # categorical measures: pooled distribution, test values with >= 10 pages over both prompts
    L.append("### Categorical measures (pooled P1 vs P5)\n")
    L.append("Each value with at least 10 pages over P1 and P5 together is tested as that value vs. everything else.\n")
    for key, lab in (("bg_hue_bucket", "background hue bucket"), ("accent_hue_bucket", "accent hue bucket"),
                     ("h1_font_group", "h1 font (grouped)"), ("body_font_group", "body font (grouped)"),
                     ("buttons_in_viewport", "buttons in first viewport"),
                     ("section_order_norm", "section order (whole sequence)")):
        c1 = Counter(r["_d"][key] for r in p1)
        c5 = Counter(r["_d"][key] for r in p5)
        vals = order_values(c1 + c5)
        if key == "buttons_in_viewport":
            vals = sorted([v for v in vals if v != NA], key=int) + ([NA] if NA in vals else [])
        tab = []
        for v in vals:
            row = [f"`{v}`", kofn(c1[v], len(p1)), kofn(c5[v], len(p5))]
            if v != NA and c1[v] + c5[v] >= 10:
                p = fisher_two_sided(c1[v], len(p1) - c1[v], c5[v], len(p5) - c5[v])
                tests.append(p)
                row += [f"{p:.3g}", verdict(p)]
            else:
                row += ["", ""]
            tab.append(row)
        if key == "section_order_norm" and len(tab) > 10:
            rest = tab[10:]
            tab = tab[:10] + [[f"({len(rest)} other sequences)", f"{sum(c1[v] for v in vals[10:])} of {len(p1)}",
                               f"{sum(c5[v] for v in vals[10:])} of {len(p5)}", "", ""]]
        L.append(f"**{lab}**\n")
        L.append(md_table(["value", "P1", "P5", "Fisher p", "label"], tab))
        L.append("")
    # per model modal values for the colour and font measures
    L.append("Most common value per model, P1 vs P5 (count out of that model's pages; ties broken alphabetically):\n")
    keys = [("bg_hue_bucket", "background"), ("accent_hue_bucket", "accent"), ("h1_font_group", "h1 font"),
            ("body_font_group", "body font")]
    tab = []
    for m in MODEL_ORDER:
        row = [DISPLAY[m]]
        for k, _ in keys:
            cells = []
            for rs in ([r for r in p1 if r["model"] == m], [r for r in p5 if r["model"] == m]):
                c = Counter(r["_d"][k] for r in rs)
                if not c:
                    cells.append("–")
                    continue
                v, n = sorted(c.items(), key=lambda t: (-t[1], t[0]))[0]
                cells.append(f"{v} {n}/{len(rs)}")
            row.append(" → ".join(cells))
        tab.append(row)
    L.append(md_table(["Model"] + [f"{lab} P1 → P5" for _, lab in keys], tab))
    L.append("")

    # numeric: buttons and file size medians
    L.append("### Numbers per model (median P1 → P5)\n")
    tab = []
    for m in MODEL_ORDER + [None]:
        a = [r for r in p1 if m is None or r["model"] == m]
        b = [r for r in p5 if m is None or r["model"] == m]
        def med(rs, col, scale=1):
            xs = [num(r, col) for r in rs]
            xs = [x for x in xs if x is not None]
            return f"{median(xs) / scale:.1f}" if xs and scale != 1 else fmt_num(median(xs))
        tab.append([DISPLAY[m] if m else "**All**", f"{len(a)} / {len(b)}",
                    f"{med(a, 'buttons_in_viewport')} → {med(b, 'buttons_in_viewport')}",
                    f"{med(a, 'file_bytes', 1000)} → {med(b, 'file_bytes', 1000)}",
                    f"{med(a, 'word_count')} → {med(b, 'word_count')}"])
    L.append(md_table(["Model", "n P1 / P5", "buttons in first viewport", "file size kB", "word count"], tab))
    L.append("")
    # text and section presence
    L.append("### Texts and sections\n")
    for col, lab in (("h1_text", "h1 text"), ("primary_button_text", "primary button text")):
        parts = []
        for name, rs in (("P1", p1), ("P5", p5)):
            have = [r for r in rs if r.get(col) not in (None, "", NA)]
            ex = Counter(norm_text(r[col]) for r in have)
            parts.append(f"{name}: {len(ex)} distinct of {len(have)}, {sum(v for v in ex.values() if v > 1)} pages share a text"
                         + (f" (most common \"{ex.most_common(1)[0][0]}\" x{ex.most_common(1)[0][1]})" if ex and ex.most_common(1)[0][1] > 1 else ""))
        L.append(f"- {lab}: " + "; ".join(parts) + ".")
    L.append("")
    types = Counter()
    for r in p1 + p5:
        for t in set(sec_split(r)):
            types[t] += 1
    tab = []
    for t in order_values(types):
        a = sum(t in set(sec_split(r)) for r in p1)
        b = sum(t in set(sec_split(r)) for r in p5)
        row = [f"`{t}`", kofn(a, len(p1)), kofn(b, len(p5))]
        if t != NA and a + b >= 10 and a + b < len(p1) + len(p5):
            p = fisher_two_sided(a, len(p1) - a, b, len(p5) - b)
            tests.append(p)
            row += [f"{p:.3g}", verdict(p)]
        else:
            row += ["", ""]
        tab.append(row)
    L.append("Section types present at least once:\n")
    L.append(md_table(["section type", "P1", "P5", "Fisher p", "label"], tab))
    L.append("")
    L.append(f"Comparisons with a p-value in this section: {len(tests)}; with p < 0.05: {sum(p < 0.05 for p in tests)}; "
             f"with p < 0.01: {sum(p < 0.01 for p in tests)}. By chance alone about {0.05 * len(tests):.1f} would fall "
             f"below 0.05 and {0.01 * len(tests):.1f} below 0.01 if P5 changed nothing (the tests overlap, so this is approximate).\n")
    return L


def section_checks(rows):
    L = ["## Measurement notes worth knowing before quoting a number\n"]
    def cnt(col):
        return ", ".join(f"`{k or '(blank)'}` {v}" for k, v in Counter(r.get(col, NA) for r in rows).most_common())
    L.append(f"- `bg_source`: {cnt('bg_source')}.")
    L.append(f"- `accent_source`: {cnt('accent_source')}.")
    L.append(f"- `color_scheme`: {cnt('color_scheme')}.")
    sn = Counter()
    for r in rows:
        for x in (r.get("source_notes") or "").split(";"):
            if x.strip():
                sn[x.strip()] += 1
    L.append("- `source_notes`: " + (", ".join(f"`{k}` {v}" for k, v in sn.most_common()) or "none") + ".")
    noh1 = sorted(r["id"] for r in rows if r.get("h1_text") in (NA, ""))
    L.append(f"- Pages without a visible h1: {len(noh1)}" + (f" ({', '.join(noh1)})" if noh1 else "") + ".")
    le = sorted(r["id"] for r in rows if r.get("load_error") not in (None, "", NA))
    L.append(f"- Pages with a load error: {len(le)}" + (f" ({', '.join(le)})" if le else "") + ".")
    retry = sorted(r["id"] for r in rows if "succeeded-on-retry" in (r.get("measure_notes") or ""))
    L.append(f"- Measured on the second try: {len(retry)}" + (f" ({', '.join(retry)})" if retry else "") + ".")
    mock = sum(1 for r in rows if r.get("mockup_present") == "y")
    L.append(f"- Pages with a detected product mock-up: {kofn(mock, len(rows))}.")
    L.append("")
    return L


def section_limits():
    return ["## Limits\n",
            "- n per cell is 5 (fewer where pages are missing). A count like 3 of 5 versus 1 of 5 is well within what "
            "repeated runs of the same model and prompt can produce.",
            "- One prompt wording per site type. Other wordings could give other pages.",
            "- P1 run 1 of every model is the pilot page, generated about an hour before the main run with the same "
            "settings; it is counted like any other run.",
            "- Pages were rendered on Linux, where Mac and Windows system fonts are not installed, so they are drawn with "
            "substitutes. Font counts use the declared font (what the page asked for), not what was drawn; the "
            "screenshots show the substitutes.",
            "- The measurer's rules were tuned on 21 project-management pages (the pilot). They may misread restaurant, "
            "photographer and law-firm pages (P2-P4), for example what counts as a feature card or a section label.",
            "- Each CLI ran with its own default instructions (README, \"Keeping the harness out of it\"). The Codex models "
            "therefore saw Codex's built-in \"Frontend Design Guidance\", including a list of suggested fonts; the Claude "
            "and Gemini models saw their own CLI's instructions. Differences between vendors may come from those "
            "instructions rather than the models.",
            "- For Gemini the kept page is the first file the model tried to create (the tool call was rejected); the "
            "reply it wrote afterwards was not used.",
            "- Everything here is correlational and descriptive: counts of what the pages contain, not causes.",
            ""]


# ---------------------------------------------------------------- runlog.md
def readme_cannot_disable():
    txt = (HERE / "README.md").read_text()
    m = re.search(r"## Keeping the harness out of it\n(.*?)\n## ", txt, re.S)
    if not m:
        return ["(README section not found)"]
    out, cur = [], None
    for line in m.group(1).splitlines():
        h = re.match(r"\*\*(.+?)\*\*", line)
        if h:
            cur = h.group(1)
            out.append(f"\n**{cur}**\n")
        elif line.startswith("- Could not disable"):
            out.append(line)
    return out


def reply_text(run, rec):
    for base in (run, run.parent / "pilot"):
        p = base / "raw" / f"{rec['id']}.a{rec['attempt']}.reply.txt"
        if p.exists():
            return p.read_text(errors="replace")
    return None


def is_quota_text(t):
    return t is not None and "Usage Limit Reached" in t[:300] and "<html" not in t.lower()


def raw_stdout_result(run, rec):
    """The `result` event of a Claude attempt's raw stdout (v1: one JSON object; v2: JSON lines)."""
    for base in (run, run.parent / "pilot"):
        f = base / "raw" / f"{rec['id']}.a{rec['attempt']}.stdout"
        if f.exists():
            for line in f.read_text(errors="replace").splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict) and e.get("type") == "result":
                    return e
    return None


def classify_v1(reply):
    t = reply or ""
    if not t.strip():
        return "empty"
    if re.search(r"<invoke|<parameter|invoke-tool|\"tool_use\"", t):
        return "written-out tool call"
    if re.search(r"\.html\b", t, re.I):
        return "prose saying an .html file was written"
    return "other text"


def write_runlog(run, out, rows, planned, page_files, escaped_all=()):
    R_all = load_jsonl(run / "runlog.jsonl")
    EV = [r for r in R_all if "event" in r]      # e.g. re-extraction of a saved page; not an attempt
    R = [r for r in R_all if "event" not in r]
    A = load_jsonl(run / "agy_log.jsonl")
    L = ["# Run log\n", f"Computed by report.py from `{run}/runlog.jsonl` ({len(R)} records), `agy_log.jsonl` "
         f"({len(A)} records) and the raw replies. All times UTC.\n"]
    for r in R:
        r["_reply"] = reply_text(run, r)
        r["_quota"] = r["vendor"] == "gemini" and not r["ok"] and is_quota_text(r["_reply"])
        r["_v1cap"] = r["vendor"] == "claude" and not r["ok"] and r.get("capture_v") != 2
        r["_counted"] = not (r["_quota"] or r["_v1cap"])

    L.append("## CLIs, models and settings\n")
    tab = []
    for m in MODEL_ORDER:
        v, mid = MODELS[m]
        used = Counter()
        for r in R:
            if r["model_id"] == mid:
                for u in r.get("models_used") or []:
                    used[u] += 1
        tab.append([DISPLAY[m], f"`{mid}`", VENDOR_CLI[v], REASONING.get(m, REASONING.get(v)),
                    ", ".join(f"`{k}` x{c}" for k, c in used.items()) or "–"])
    L.append(md_table(["Model", "exact model id", "CLI", "reasoning", "model reported by the CLI (Claude: modelUsage keys)"], tab))
    L.append("\nTemperature cannot be set in any of the three CLIs and was not set.\n")
    side = []
    for r in R:
        if r["vendor"] != "claude" or r["model_id"] == "claude-haiku-5-5":
            continue
        e = raw_stdout_result(run, r)
        if e and isinstance(e.get("modelUsage"), dict) and "claude-haiku-5-5" in e["modelUsage"]:
            side.append(e["modelUsage"]["claude-haiku-5-5"].get("outputTokens", 0))
    if side:
        L.append(f"Claude Code also made a small `claude-haiku-5-5` call inside the Fable, Opus and Sonnet sessions "
                 f"(found in {len(side)} raw session records; {min(side)}-{max(side)} output tokens each, median "
                 f"{median(side):g}). This is the CLI's own side request, not the page; `usage.output_tokens` in the "
                 "run log is the main model's count only. (For Haiku sessions the two cannot be told apart.)\n")
    gem_models = Counter()
    for f in sorted((run / "agy").glob("*.summary.json")):
        try:
            gem_models[json.loads(f.read_text() or "{}").get("model")] += 1
        except json.JSONDecodeError:
            gem_models["(unreadable summary)"] += 1
    if gem_models:
        L.append("Model reported by the Antigravity server in each Gemini call summary (`agy/*.summary.json`, main run, "
                 "including usage-limit calls): " + ", ".join(f"`{k}` x{v}" for k, v in gem_models.items()) + ".\n")

    L.append("### Exact command lines\n")
    L.append("Claude Code and Codex: as logged in `runlog.jsonl` (`cmd`, prompt removed; it is passed as the argument "
             "after `-p` for Claude and as the last argument for Codex; work-folder paths replaced by `<workdir>`).\n")
    cmds = Counter()
    first_last = {}
    for r in R:
        if "cmd" not in r:
            continue
        c = " ".join(("''" if x == "" else (x if re.fullmatch(r"[\w@%+=:,./-]+", x) else "'" + x.replace("'", "'\\''") + "'"))
                     for x in r["cmd"])
        c = re.sub(r"/data/scratch/ai-sameness/\w+/work/[^ ]+?/last\.txt", "<workdir>/last.txt", c)
        c = c.replace(r["model_id"], "<model>")
        key = (r["vendor"], c)
        cmds[key] += 1
        s = r["started"]
        lo, hi = first_last.get(key, (s, s))
        first_last[key] = (min(lo, s), max(hi, s))
    for (v, c), n in sorted(cmds.items()):
        lo, hi = first_last[(v, c)]
        L.append(f"- {VENDOR_CLI[v]}, {n} attempts, {lo} to {hi}:\n  `{c}`")
    L.append("  - Codex environment: `CODEX_HOME=/data/scratch/ai-sameness/codex-home` (clean; only a link to the login file).")
    L.append("  - Claude: the `--output-format json` form is capture v1 (only the final text block was read); "
             "`--output-format stream-json --verbose` is capture v2 (every text block). See incident (a).")
    L.append("- Antigravity (Gemini), main run, from `agy_run.py` (one fresh server per call):\n  "
             "`python3 agy_once.py --model <model> --prompt-file agy/<P>.txt --out agy/<id>.a<n>.reply.txt "
             "--log agy/<id>.a<n>.events.jsonl --timeout 1800 --tools create_file`\n  then "
             "`python3 gen.py ingest --name main --id <id> --file <first create_file payload, else the reply text>`. "
             "The pilot Gemini pages (P1 run 1) were made with the same client (`agy_once.py`). Note: `gen.py ingest` "
             "writes `\"via\": \"t3 delegate_task\"` on every Gemini record; that field is a fixed string and does not "
             "describe how the page was made.")
    L.append("")

    L.append("## Run dates\n")
    for v in ("claude", "codex", "gemini"):
        st = sorted(r["started"] for r in R if r["vendor"] == v)
        if st:
            L.append(f"- {VENDOR_CLI[v]}: first record {st[0]}, last record {st[-1]}"
                     + (" (Gemini `started` is the time the reply was ingested)" if v == "gemini" else "") + ".")
    L.append("")

    # Gemini timings from agy_log (non-quota calls) and pilot summaries
    agy_secs = defaultdict(list)
    for a in A:
        if a.get("quota"):
            continue
        if a.get("kept") == "reply text":
            # an early-runner usage-limit call is logged with kept=reply text; check its reply
            t = None
            p = run / "agy" / f"{a['id']}.a{a['attempt']}.reply.txt"
            if p.exists():
                t = p.read_text(errors="replace")
            if is_quota_text(t):
                continue
        agy_secs[a["id"]].append(a.get("seconds"))
    pilot_agy = run.parent / "pilot" / "agy"
    for m in MODEL_ORDER:
        if MODELS[m][0] != "gemini":
            continue
        f = pilot_agy / f"{m}__P1__r1.summary.json"
        if f.exists():
            try:
                agy_secs[f"{m}__P1__r1"].append(json.loads(f.read_text()).get("seconds"))
            except json.JSONDecodeError:
                pass

    L.append("## Per model\n")
    L.append("Counting rule (the one `gen.py` and `agy_run.py` apply): every record in `runlog.jsonl` is an attempt, except "
             "(a) Claude records that failed under capture v1 and (b) Gemini replies that were only the account's "
             "usage-limit notice. \"First attempt failed\" = the page's first counted attempt produced no page. "
             "Output tokens = sum of `usage.output_tokens` over all records as reported by each CLI (Claude includes "
             "thinking tokens, Codex includes reasoning tokens; the two are not directly comparable; the Antigravity "
             "server reports no output-token count). Wall time = sum of `seconds` over the counted attempts (Gemini: "
             "`agy_log.jsonl` and the pilot call summaries; usage-limit calls and the waits after them excluded).\n")
    tab = []
    for m in MODEL_ORDER:
        v, mid = MODELS[m]
        rs = [r for r in R if r["model_id"] == mid]
        ids = {j for j, jj in planned.items() if jj["model"] == m}
        pages = len([i for i in ids if i in page_files])
        measured = sum(1 for r in rows if r["model"] == m)
        counted = [r for r in rs if r["_counted"]]
        excluded = len(rs) - len(counted)
        byid = defaultdict(list)
        for r in counted:
            byid[r["id"]].append(r)
        first_fail = sum(1 for i, xs in byid.items() if not sorted(xs, key=lambda r: r["attempt"])[0]["ok"])
        toks = [r["usage"]["output_tokens"] for r in rs if isinstance(r.get("usage"), dict) and "output_tokens" in r["usage"]]
        if v == "gemini":
            secs = [s for i in ids for s in agy_secs.get(i, []) if s is not None]
        else:
            secs = [r["seconds"] for r in counted if isinstance(r.get("seconds"), (int, float))]
        okt = [r["usage"]["output_tokens"] for r in rs if r["ok"] and isinstance(r.get("usage"), dict) and "output_tokens" in r["usage"]]
        tab.append([DISPLAY[m], f"{pages} of {len(ids)}", measured, len(rs), excluded, len(counted),
                    f"{first_fail} of {len(byid)}", f"{sum(toks):,}" if toks else "not reported",
                    f"{median(okt):,.0f}" if okt else "–",
                    f"{sum(secs) / 3600:.2f} h ({len(secs)} calls)" if secs else "not recorded",
                    f"{median(secs):.0f} s" if secs else "–"])
    L.append(md_table(["Model", "pages on disk", "rows in CSV", "records in log", "not counted", "counted attempts",
                       "first attempt failed", "output tokens (all records)", "median output tokens per page",
                       "wall time", "median s per call"], tab))
    L.append("")
    cx = [r for r in R if r["vendor"] == "codex"]
    ran = Counter(r["model_id"] for r in cx if any(i == "command_execution" for i in (r.get("items") or [])))
    L.append("Codex attempts in which the model ran a shell command in the read-only sandbox before answering: " +
             ", ".join(f"`{MODELS[m][1]}` {ran[MODELS[m][1]]} of {sum(1 for r in cx if r['model_id'] == MODELS[m][1])}"
                       for m in MODEL_ORDER if MODELS[m][0] == "codex") + ".\n")
    gk = Counter(a.get("kept") for a in A if a.get("ok"))
    L.append("Gemini pages ingested by `agy_run.py`, by what was kept: " + ", ".join(f"{k} {v}" for k, v in gk.items()) + ".\n")

    # failures
    L.append("## Every failed attempt\n")
    fails = [r for r in R if not r["ok"] and not r["_quota"]]
    tab = []
    for r in sorted(fails, key=lambda r: r["started"]):
        if r.get("exit") == "timeout":
            reason = f"timed out after {r.get('seconds')} s with no output"
        elif r["capture"] == "empty":
            reason = "empty reply"
        else:
            reason = "reply contained no HTML document"
        if r.get("exit") not in (0, None, "timeout"):
            reason += f"; exit code {r['exit']}"
        if r["_v1cap"]:
            reason += "; capture v1 (final text block only), not counted"
        ot = r.get("usage", {}).get("output_tokens") if isinstance(r.get("usage"), dict) else None
        extra = ""
        if r["vendor"] == "codex" and r.get("items"):
            extra = " items: " + ",".join(r["items"])
        tab.append([r["id"], r["attempt"], r["started"], reason, r.get("reply_chars"),
                    f"{ot:,}" if ot is not None else "–", scrub(r["_reply"], 140) + extra])
    L.append(md_table(["id", "attempt", "started", "reason", "reply chars", "output tokens", "reply begins"], tab))
    L.append("")
    q = [r for r in R if r["_quota"]]
    qids = sorted({r["id"] for r in q})
    L.append(f"Gemini usage-limit notices saved as replies (not counted as attempts): {len(q)} records for {len(qids)} pages, "
             f"ingested {min(r['started'] for r in q) if q else '–'} to {max(r['started'] for r in q) if q else '–'}. "
             f"Pages: {', '.join(qids)}.\n")
    qa = [a for a in A if a.get("quota")]
    L.append(f"Usage-limit calls caught by the current `agy_run.py` before ingest (logged in `agy_log.jsonl` with "
             f"`quota: true`, never saved as replies): {len(qa)}" +
             (f", for {len({a['id'] for a in qa})} pages, total wait {sum(a.get('wait_s', 0) for a in qa) / 3600:.1f} h" if qa else "") + ".\n")

    # incidents
    L.append("## Incidents\n")
    v1 = [r for r in R if r["_v1cap"]]
    v1ids = sorted({r["id"] for r in v1})
    twice = [i for i in v1ids if sum(1 for r in v1 if r["id"] == i) >= 2]
    regen = [i for i in twice if any(r["id"] == i and r["ok"] and r.get("capture_v") == 2 for r in R)]
    kept_after = [i for i in v1ids if i not in twice and any(r["id"] == i and r["ok"] for r in R)]
    v1_tok = [r["usage"]["output_tokens"] for r in v1 if isinstance(r.get("usage"), dict) and "output_tokens" in r["usage"]]
    v1_chars = [r.get("reply_chars") or 0 for r in v1]
    pilot_v1 = [r for r in v1 if r.get("from_pilot")]
    timeouts = [f"{r['id']} a{r['attempt']}" for r in v1 if r.get("exit") == "timeout"]
    nonzero = [f"{r['id']} a{r['attempt']} (exit {r['exit']})" for r in v1 if r.get("exit") not in (0, "timeout")]
    v2_first = min((r["started"] for r in R if r.get("capture_v") == 2), default="–")
    kinds = Counter(classify_v1(r["_reply"]) for r in v1 if r.get("exit") != "timeout" and r.get("exit") in (0, None))
    L.append("**(a) Claude capture v1.** Until capture v2 (first v2 record: " + v2_first + "), the runner read only the final "
             "text block of a Claude reply (`--output-format json`, `result`). With no tools available, a model that wrote the "
             "page inside a pretend file-write and then ended with a short note left no HTML in that final block, so the "
             f"attempt was logged as failed. From the logs: {len(v1)} Claude attempts failed under v1 "
             f"({len(pilot_v1)} of them in the pilot) across {len(v1ids)} pages; by model: " +
             ", ".join(f"`{k}` {c}" for k, c in Counter(r['model_id'] for r in v1).most_common()) + ". "
             f"Their saved replies were short (median {median(v1_chars):,.0f} characters, max {max(v1_chars) if v1_chars else 0:,}) "
             f"while the CLI reported a median of {median(v1_tok):,.0f} output tokens for them (min {min(v1_tok) if v1_tok else 0:,}), "
             "consistent with a page having been written and not kept (the v1 output does not contain the earlier text "
             "blocks, so this cannot be confirmed from the saved files). What the saved final blocks of the exit-0 attempts "
             "contain: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()) + ". These attempts were not counted against the "
             f"two-attempt limit. Pages that had failed twice under v1 and were generated again under v2: {len(regen)} "
             f"({', '.join(regen)}). Pages that failed once under v1 and had already succeeded on the rerun, and were kept: "
             f"{len(kept_after)} ({', '.join(kept_after)}).")
    if timeouts or nonzero:
        L.append("\nNot every excluded v1 failure was a capture problem: " +
                 "; ".join(([f"timeout with no output: {', '.join(timeouts)}"] if timeouts else []) +
                           ([f"non-zero exit: {', '.join(nonzero)}"] if nonzero else [])) +
                 ". The rule excludes all Claude v1 failures, so these were not counted either.")
    v2f = [f"{r['id']} a{r['attempt']}" for r in R if r["vendor"] == "claude" and not r["ok"] and r.get("capture_v") == 2]
    L.append(f"\nClaude attempts that failed under capture v2 (counted normally): {len(v2f)}" + (f" ({', '.join(v2f)})" if v2f else "") + ".\n")
    after = [i for i in qids if i in page_files]
    pending = [i for i in qids if i not in page_files]
    per = Counter(r["id"] for r in q)
    L.append(f"**(b) Gemini usage limit.** The account hit its usage limit. {len(qids)} pages got usage-limit notices "
             f"(\"Usage Limit Reached\") saved as replies: " +
             ", ".join(f"{c} notices for {n} pages" for c, n in sorted(Counter(per.values()).items())) +
             f"; by model: " + ", ".join(f"`{k}` {v}" for k, v in Counter(r['model_id'] for r in q).most_common()) +
             ". The notice never reached the model, so these were not counted as attempts (`agy_run.py` since commit "
             f"42d6189), and the pages were retried after the limit reset. Of the {len(qids)}: {len(after)} have a page now, "
             f"{len(pending)} do not yet" + (f" ({', '.join(pending)})" if pending else "") + ".\n")

    last_ok = {}
    for r in R:
        if r["ok"]:
            last_ok[r["id"]] = r
    if escaped_all:
        tab = []
        for i in escaped_all:
            r = last_ok.get(i, {})
            tab.append([i, r.get("attempt", "–"), r.get("started", "–"), r.get("capture", "–"),
                        r.get("capture_v") or ("1" if r.get("vendor") == "claude" else "–")])
        L.append(f"**(c) Pages saved in JSON-escaped form** (found by report.py, not by the runner). {len(escaped_all)} page "
                 "files hold HTML that is still a JSON string: literal `\\n` and `\\\"` instead of line breaks and quotes. "
                 "In each case the reply contained a written-out tool call whose `content` field held the page, and the "
                 "runner's `bare` capture saved that text as it stood. Browsers show these pages as unstyled text. "
                 "The record that produced each page:\n")
        L.append(md_table(["id", "attempt", "started", "capture", "capture version"], tab))
        L.append("")
    if EV:
        L.append(f"**(d) Pages re-extracted from their saved reply.** {len(EV)} page files were replaced by a second "
                 "extraction from the same saved reply. No new generation was made and the reply files were not changed. "
                 "The earlier page files are kept in `pages_escaped_v1/` in the run folder. Two causes: the model wrote "
                 "its page inside a written-out tool call as a JSON string, which the first capture saved still escaped "
                 "(capture `json-string` decodes it); or the saved file ran past the first closing html tag into "
                 "written-out tool-call text (capture `bare` now stops at the first closing tag).\n")
        L.append(md_table(["id", "reply file", "capture", "note"],
                          [[e.get("id"), e.get("from"), e.get("capture"), e.get("note")] for e in EV]))
        L.append("")
    L.append("## Could not be disabled (copied from README, \"Keeping the harness out of it\")\n")
    L.extend(readme_cannot_disable())
    L.append("")
    (out / "runlog.md").write_text("\n".join(L) + "\n")


# ---------------------------------------------------------------- walls
SHEET_CSS = """
body{margin:0;background:#1b1b1f;font-family:'DejaVu Sans',sans-serif;color:#eee}
.hdr{padding:18px 24px 6px;font-size:30px;font-weight:bold}
.sub{padding:0 24px 14px;font-size:18px;color:#bbb}
.grid{display:grid;gap:24px;padding:0 24px 24px}
.band{background:#26262c;border-radius:6px;padding:12px 16px 14px}
.bt{font-size:26px;font-weight:bold;margin:0 0 8px}
.bt span{font-weight:normal;color:#aaa;font-size:18px}
.row{display:flex;gap:8px;margin-top:6px}
.cell{width:TWpx}
.lab{font-size:14px;line-height:18px;height:18px;color:#ccc;white-space:nowrap;overflow:hidden}
.img{width:TWpx;height:THpx;display:block;background:#fff}
.miss{width:TWpx;height:THpx;box-sizing:border-box;border:2px dashed #666;color:#999;font-size:18px;
display:flex;align-items:center;justify-content:center;text-align:center;background:#202024}
.grp{font-size:18px;color:#ddd;font-weight:bold;margin:0 0 2px}
.prow{display:flex;gap:16px;align-items:flex-start}
.mlab{width:230px;font-size:22px;font-weight:bold;padding-top:60px}
.mlab span{display:block;font-weight:normal;color:#aaa;font-size:15px}
.gsep{width:28px}
"""


def thumb(run_shots, thumbs, jid, tw, th):
    src = run_shots / f"{jid}.viewport.png"
    if not src.exists():
        return None
    dst = thumbs / f"{jid}.{tw}.png"
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                        f"scale={tw}:{th}:flags=lanczos", "-frames:v", "1", str(dst)], check=True)
    return dst


def cell_html(jid, p, r, src, status, flagged=()):
    lab = f"{p} r{r}" + (" · JSON-escaped capture" if jid in flagged else "")
    if src:
        return f'<div class="cell"><div class="lab">{lab}</div><img class="img" src="{src.as_uri()}"></div>'
    return (f'<div class="cell"><div class="lab">{lab}</div><div class="miss">{html.escape(lab)}<br>'
            f'{html.escape(status)}</div></div>')


def slot_status(jid, page_files, measured):
    if jid not in page_files:
        return "no page (not generated)"
    if jid not in measured:
        return "page exists, not measured"
    return "no screenshot"


def render(sheet_html, out_png, w, h):
    p = subprocess.run(["node", str(HERE / "wall.mjs"), str(sheet_html), str(out_png), str(w), str(h)],
                       cwd=HERE, capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"wall.mjs failed: {p.stderr}")
    print(p.stdout.strip(), p.stderr.strip(), file=sys.stderr)


def small_jpg(png, jpg, width=4000):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(png), "-vf", f"scale={width}:-2:flags=lanczos",
                    "-q:v", "3", "-frames:v", "1", str(jpg)], check=True)


def luma_range(png):
    """(YMIN, YMAX, YAVG) of an image via ffmpeg signalstats, for spotting blank thumbnails."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(png), "-vf",
                        "signalstats,metadata=print:file=-", "-f", "null", "-"], capture_output=True, text=True)
    vals = dict(re.findall(r"lavfi\.signalstats\.(YMIN|YMAX|YAVG)=([\d.]+)", p.stdout))
    try:
        return float(vals["YMIN"]), float(vals["YMAX"]), float(vals["YAVG"])
    except KeyError:
        return None


def build_walls(run, out, rows, page_files, shots, tw, flagged=(), excluded=()):
    th = round(tw * 900 / 1440)
    thumbs = out / "thumbs"
    sheets = out / "sheets"
    thumbs.mkdir(parents=True, exist_ok=True)
    sheets.mkdir(parents=True, exist_ok=True)
    measured = {r["id"] for r in rows} | set(excluded)
    css = SHEET_CSS.replace("TW", str(tw)).replace("TH", str(th))
    srcs, flat = {}, []
    for m in MODEL_ORDER:
        for p in PROMPT_ORDER:
            for r in range(1, 6):
                jid = f"{m}__{p}__r{r}"
                srcs[jid] = thumb(shots, thumbs, jid, tw, th)

    # wall.png: 4 x 3 bands, each 4 prompts (rows) x 5 runs
    band_w = 5 * tw + 4 * 8 + 32
    cols = 4
    bands = []
    for m in MODEL_ORDER:
        rows_html = []
        for p in ("P1", "P2", "P3", "P4"):
            cells = [cell_html(f"{m}__{p}__r{r}", p, r, srcs[f"{m}__{p}__r{r}"],
                               slot_status(f"{m}__{p}__r{r}", page_files, measured), flagged) for r in range(1, 6)]
            rows_html.append('<div class="row">' + "".join(cells) + "</div>")
        bands.append(f'<div class="band"><div class="bt">{DISPLAY[m]} <span>{MODELS[m][1]}</span></div>' +
                     "".join(rows_html) + "</div>")
    W = 24 * 2 + cols * band_w + (cols - 1) * 24
    band_h = 12 + 34 + 8 + 4 * (6 + 18 + th) + 14
    H = 18 + 36 + 6 + 18 + 14 + 3 * band_h + 2 * 24 + 24
    doc = (f"<!doctype html><meta charset=utf-8><style>{css}.grid{{grid-template-columns:repeat({cols},{band_w}px)}}"
           f"</style><div class=hdr>First viewport (1440x900) of every P1-P4 page, one band per model</div>"
           f"<div class=sub>Rows in each band: P1 project management app, P2 Italian restaurant, P3 photographer "
           f"portfolio, P4 small law firm; runs r1-r5 left to right. Empty dashed slot = no screenshot.</div>"
           f"<div class=grid>{''.join(bands)}</div>")
    (sheets / "wall.html").write_text(doc)
    render(sheets / "wall.html", out / "wall.png", W, H)
    small_jpg(out / "wall.png", out / "wall_small.jpg")

    # wall_p5.png: per model row, P1 r1-5 | P5 r1-5
    prow = []
    for m in MODEL_ORDER:
        g = []
        for p in ("P1", "P5"):
            cells = [cell_html(f"{m}__{p}__r{r}", p, r, srcs[f"{m}__{p}__r{r}"],
                               slot_status(f"{m}__{p}__r{r}", page_files, measured), flagged) for r in range(1, 6)]
            g.append('<div><div class="grp">' + ("P1" if p == "P1" else "P5 (+ \"Don't make it look AI-generated.\")") +
                     '</div><div class="row" style="margin-top:0">' + "".join(cells) + "</div></div>")
        prow.append(f'<div class="band prow"><div class="mlab">{DISPLAY[m]}<span>{MODELS[m][1]}</span></div>'
                    + g[0] + '<div class="gsep"></div>' + g[1] + "</div>")
    W2 = 24 * 2 + 32 + 230 + 16 + 2 * (5 * tw + 4 * 8) + 16 + 28 + 16
    row_h = 12 + 22 + 2 + 18 + th + 14
    H2 = 18 + 36 + 6 + 18 + 14 + 12 * row_h + 11 * 24 + 24
    doc = (f"<!doctype html><meta charset=utf-8><style>{css}.grid{{grid-template-columns:{W2 - 48}px}}</style>"
           f"<div class=hdr>P1 vs P5, first viewport (1440x900), one row per model</div>"
           f"<div class=sub>P1: \"{html.escape(PROMPTS['P1'])}\" &nbsp; P5: the same + \"Don't make it look "
           f"AI-generated.\" &nbsp; Runs r1-r5 left to right. Empty dashed slot = no screenshot.</div>"
           f"<div class=grid>{''.join(prow)}</div>")
    (sheets / "wall_p5.html").write_text(doc)
    render(sheets / "wall_p5.html", out / "wall_p5.png", W2, H2)
    small_jpg(out / "wall_p5.png", out / "wall_p5_small.jpg")

    # blank-thumbnail check on the source screenshots
    flags = []
    for jid, src in srcs.items():
        if src is None:
            continue
        lr = luma_range(src)
        if lr and lr[1] - lr[0] < 12:
            flags.append((jid, lr))
    missing = sorted(j for j, s in srcs.items() if s is None and not j.split("__")[1] == "P5")
    missing5 = sorted(j for j, s in srcs.items() if s is None and j.split("__")[1] == "P5")
    return {"thumb": f"{tw}x{th}", "flat": flags, "missing_p1_p4": missing, "missing_p5": missing5,
            "sizes": {f: (out / f).stat().st_size for f in ("wall.png", "wall_small.jpg", "wall_p5.png", "wall_p5_small.jpg")},
            "dims": {"wall.png": (W, H), "wall_p5.png": (W2, H2)}}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run folder: jobs.jsonl, pages/, runlog.jsonl, agy_log.jsonl, shots/")
    ap.add_argument("--results", required=True, help="measurer CSV")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shots", help="screenshot folder (default <run>/shots)")
    ap.add_argument("--thumb-width", type=int, default=360)
    ap.add_argument("--no-walls", action="store_true")
    ap.add_argument("--exclude", help="ids to leave out of every count, comma-separated, or @file with one id per "
                                      "line; listed in summary.md. Default: none (every CSV row is counted)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    run, jobs, planned, cols, rows, other_arm, page_files, excluded, escaped_all = load(args)

    # results.csv: measurer columns, vendor and model_id inserted after model, font groups appended
    i = cols.index("model") + 1
    ocols = cols[:i] + ["vendor", "model_id"] + cols[i:] + ["h1_font_group", "body_font_group", "page_json_escaped"]
    order = {jid: k for k, jid in enumerate(sorted(planned, key=lambda j: (MODEL_ORDER.index(planned[j]["model"]),
                                                                        planned[j]["prompt"], planned[j]["run"])))}
    rows.sort(key=lambda r: (order.get(r["id"], 10 ** 6), r["id"]))
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ocols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    L = ["# Do AI-built websites all look the same? Summary of measurements\n",
         f"Generated by `report.py` from `{args.results}` ({len(rows)} rows). Every count below can be recomputed from "
         f"`results.csv` in this folder (same rows, plus `vendor`, `model_id`, `h1_font_group`, `body_font_group`, `page_json_escaped`). "
         "Column definitions: MEASURES.md in the repo. Run details and failures: runlog.md.\n"]
    L += section_run(rows, planned, page_files, other_arm, args.results, cols, excluded)
    L += section_problems(rows, excluded, escaped_all)
    L += section_measures(rows)
    tl, top3, top3p = section_traits(rows)
    L += tl
    L += section_break(rows, top3, top3p)
    L += section_p5(rows)
    L += section_checks(rows)
    L += section_limits()
    (out / "summary.md").write_text("\n".join(L) + "\n")
    write_runlog(run, out, rows, planned, page_files, escaped_all)
    print(f"wrote {out}/results.csv ({len(rows)} rows), summary.md, runlog.md", file=sys.stderr)

    if not args.no_walls:
        info = build_walls(run, out, rows, page_files, Path(args.shots) if args.shots else run / "shots", args.thumb_width,
                           set(escaped_all), excluded)
        print(json.dumps(info, indent=1), file=sys.stderr)
        (out / "walls.json").write_text(json.dumps(info, indent=1) + "\n")


if __name__ == "__main__":
    main()
