#!/usr/bin/env python3
"""Generate landing pages for the 'do AI-built websites look the same' study.

One fresh CLI session per page, one turn, run from a new empty folder outside
any git repo. The prompt is sent verbatim. Raw output is always kept; the HTML
is captured from it without edits.

  gen.py plan  --name pilot --prompts P1 --runs 1 [--arms default,minimal]
  gen.py run   --name pilot [--workers 2] [--vendor claude,codex]
  gen.py ingest --name pilot --id <id> --file <reply.txt>   (Gemini via T3)
  gen.py status --name pilot

Data lives under /data/scratch/ai-sameness/<name>/.
"""
import argparse, json, os, random, re, subprocess, sys, tempfile, time, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path("/data/scratch/ai-sameness")
CODEX_HOME = ROOT / "codex-home"      # clean: only a link to the login file
SYS_FILE = ROOT / "sys.txt"           # "You are a helpful assistant."
MIN_SYS = "You are a helpful assistant."

PROMPTS = {
    "P1": "Build a landing page for a project management app. Return a single self-contained HTML file.",
    "P2": "Build a landing page for a neighborhood Italian restaurant. Return a single self-contained HTML file.",
    "P3": "Build a portfolio site for a freelance photographer. Return a single self-contained HTML file.",
    "P4": "Build a landing page for a small law firm. Return a single self-contained HTML file.",
    "P5": "Build a landing page for a project management app. Return a single self-contained HTML file. Don't make it look AI-generated.",
}

# label -> (vendor, exact model id)
MODELS = {
    "fable-5.2":    ("claude", "claude-fable-5-2"),
    "opus-5.5":     ("claude", "claude-opus-5-5"),
    "sonnet-5.5":   ("claude", "claude-sonnet-5-5"),
    "haiku-5.5":    ("claude", "claude-haiku-5-5"),
    "gpt-6.2-sol":  ("codex", "gpt-6.2-sol"),
    "gpt-6.2-astra": ("codex", "gpt-6.2-astra"),
    "gpt-6.1-sol":  ("codex", "gpt-6.1-sol"),
    "gpt-6.1-terra": ("codex", "gpt-6.1-terra"),
    "gpt-6-luna":   ("codex", "gpt-6-luna"),
    "neon":         ("gemini", "gemini-4-neon-high"),
    "argon":        ("gemini", "gemini-4-argon-high"),
    "hydrogen":     ("gemini", "gemini-4-hydrogen-high"),
}

CODEX_OFF = ["apps", "plugins", "remote_plugin"]  # user-level connectors/plugins


def claude_cmd(model, prompt, arm):
    cmd = ["claude", "-p", prompt, "--model", model,
           "--setting-sources", "", "--strict-mcp-config", "--tools", "",
           "--disable-slash-commands", "--no-session-persistence",
           "--output-format", "stream-json", "--verbose"]
    if arm == "minimal":
        cmd += ["--system-prompt", MIN_SYS]
    return cmd, dict(os.environ)


def codex_cmd(model, prompt, arm, workdir):
    cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral",
           "--ignore-user-config", "--ignore-rules", "-s", "read-only",
           "-m", model, "-c", 'web_search="disabled"']
    for f in CODEX_OFF:
        cmd += ["--disable", f]
    if arm == "minimal":
        cmd += ["-c", f'model_instructions_file="{SYS_FILE}"',
                "-c", "include_permissions_instructions=false",
                "-c", "include_environment_context=false",
                "-c", "include_collaboration_mode_instructions=false"]
    cmd += ["--json", "-o", str(workdir / "last.txt"), prompt]
    env = dict(os.environ)
    env["CODEX_HOME"] = str(CODEX_HOME)
    return cmd, env


def extract_html(text):
    """Capture the HTML document from a reply without changing it."""
    if not text:
        return None, "empty"
    fences = re.findall(r"```(?:html|HTML)?[ \t]*\n(.*?)```", text, re.S)
    docs = [f for f in fences if re.search(r"<html|<!doctype", f, re.I)]
    if docs:
        return max(docs, key=len).strip() + "\n", "fence"
    # first complete document: a model may write it out once (often inside a pretend
    # file-write call when it has no tools) and then again or summarise it afterwards
    m = re.search(r"(<!doctype html.*?</html>|<html.*?</html>)", text, re.S | re.I)
    if m:
        return m.group(1).strip() + "\n", "bare"
    return None, "no-html"


def run_one(job, base):
    jid = job["id"]
    vendor, model = MODELS[job["model"]]
    prompt = PROMPTS[job["prompt"]]
    last = None
    for attempt in range(job.get("first_attempt", 1), job.get("first_attempt", 1) + job.get("attempts_left", 2)):
        workdir = Path(tempfile.mkdtemp(prefix=f"{jid}.a{attempt}.", dir=base / "work"))
        if vendor == "claude":
            cmd, env = claude_cmd(model, prompt, job["arm"])
        else:
            cmd, env = codex_cmd(model, prompt, job["arm"], workdir)
        t0 = time.time()
        rec = {"id": jid, "attempt": attempt, "vendor": vendor, "model_id": model,
               "arm": job["arm"], "prompt": job["prompt"], "run": job["run"],
               "workdir": str(workdir), "cmd": cmd[:-1] if vendor == "codex" else [c for c in cmd if c != prompt],
               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))}
        try:
            p = subprocess.run(cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=1500)
            rec["exit"] = p.returncode
            out, err = p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            rec["exit"] = "timeout"
            out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        rec["seconds"] = round(time.time() - t0, 1)
        tag = f"{jid}.a{attempt}"
        (base / "raw" / f"{tag}.stdout").write_text(out)
        (base / "raw" / f"{tag}.stderr").write_text(err)
        reply = ""
        if vendor == "claude":
            # capture v2: every text block the model emitted, in order. v1 kept only the
            # final block ("result"), which dropped pages written inside a pretend file-write.
            rec["capture_v"] = 2
            texts = []
            for line in out.splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") == "assistant":
                    for c in (e.get("message") or {}).get("content", []):
                        if c.get("type") == "text":
                            texts.append(c.get("text") or "")
                elif e.get("type") == "result":
                    rec["usage"] = {k: v for k, v in (e.get("usage") or {}).items() if "tokens" in k}
                    rec["num_turns"] = e.get("num_turns")
                    rec["is_error"] = e.get("is_error")
                    rec["models_used"] = list((e.get("modelUsage") or {}).keys())
            reply = "\n".join(texts)
            rec["text_blocks"] = len(texts)
        else:
            lp = workdir / "last.txt"
            reply = lp.read_text() if lp.exists() else ""
            items = []
            for line in out.splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") == "turn.completed":
                    rec["usage"] = e.get("usage")
                if e.get("type") == "item.completed":
                    items.append((e.get("item") or {}).get("type"))
                if e.get("type") == "error":
                    rec.setdefault("errors", []).append(str(e.get("message"))[:300])
            rec["items"] = items  # shows any tool calls the model made
        (base / "raw" / f"{tag}.reply.txt").write_text(reply)
        rec["files_in_workdir"] = sorted(x.name for x in workdir.iterdir() if x.name != "last.txt")
        html, how = extract_html(reply)
        rec["capture"] = how
        rec["reply_chars"] = len(reply)
        if html:
            (base / "pages" / f"{jid}.html").write_text(html)
            rec["ok"] = True
        else:
            rec["ok"] = False
        with LOCK:
            with open(base / "runlog.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"[{time.strftime('%H:%M:%S')}] {tag} ok={rec['ok']} {rec['seconds']}s capture={how}", flush=True)
        last = rec
        if rec["ok"]:
            break
    return last


LOCK = threading.Lock()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["plan", "run", "ingest", "status"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--prompts", default="P1,P2,P3,P4,P5")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--arms", default="default")
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--seed", type=int, default=20261123)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--vendor", default="claude,codex")
    ap.add_argument("--id")
    ap.add_argument("--file")
    a = ap.parse_args()
    base = ROOT / a.name
    for d in ("work", "raw", "pages"):
        (base / d).mkdir(parents=True, exist_ok=True)
    plan = base / "jobs.jsonl"

    if a.action == "plan":
        if plan.exists():
            sys.exit(f"{plan} exists; not overwriting")
        jobs = []
        for arm in a.arms.split(","):
            for m in a.models.split(","):
                if arm == "minimal" and MODELS[m][0] == "gemini":
                    continue  # no system prompt control through T3
                for p in a.prompts.split(","):
                    for r in range(1, a.runs + 1):
                        suffix = "" if arm == "default" else f"__{arm}"
                        jobs.append({"id": f"{m}__{p}__r{r}{suffix}", "model": m,
                                     "prompt": p, "run": r, "arm": arm})
        random.Random(a.seed).shuffle(jobs)
        for i, j in enumerate(jobs):
            j["order"] = i
        plan.write_text("".join(json.dumps(j) + "\n" for j in jobs))
        print(f"{len(jobs)} jobs -> {plan}")
        return

    jobs = [json.loads(l) for l in plan.read_text().splitlines()]

    if a.action == "ingest":
        job = next(j for j in jobs if j["id"] == a.id)
        reply = Path(a.file).read_text()
        n = len(list((base / "raw").glob(f"{a.id}.a*.reply.txt"))) + 1
        (base / "raw" / f"{a.id}.a{n}.reply.txt").write_text(reply)
        html, how = extract_html(reply)
        rec = {"id": a.id, "attempt": n, "vendor": "gemini", "model_id": MODELS[job["model"]][1],
               "arm": job["arm"], "prompt": job["prompt"], "run": job["run"],
               "capture": how, "reply_chars": len(reply), "ok": bool(html), "via": "t3 delegate_task",
               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if html:
            (base / "pages" / f"{a.id}.html").write_text(html)
        with open(base / "runlog.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(rec)
        return

    done = {p.stem for p in (base / "pages").glob("*.html")}
    tried, counted = {}, {}
    if (base / "runlog.jsonl").exists():
        for l in (base / "runlog.jsonl").read_text().splitlines():
            r = json.loads(l)
            tried[r["id"]] = max(tried.get(r["id"], 0), r["attempt"])
            # Claude attempts made with capture v1 that "failed" were capture failures on our
            # side (the page was produced but not saved), so they do not use up the two attempts.
            if not (r.get("vendor") == "claude" and not r.get("ok") and r.get("capture_v") != 2):
                counted[r["id"]] = counted.get(r["id"], 0) + 1

    if a.action == "status":
        print(f"jobs={len(jobs)} pages={len(done)}")
        for j in jobs:
            if j["id"] not in done:
                print("  todo", j["order"], j["id"], "attempts", tried.get(j["id"], 0))
        return

    vendors = a.vendor.split(",")
    # a job that already used both attempts is not tried again
    todo = [j for j in jobs if j["id"] not in done and counted.get(j["id"], 0) < 2
            and MODELS[j["model"]][0] in vendors]
    for j in todo:
        j["first_attempt"] = tried.get(j["id"], 0) + 1
        j["attempts_left"] = 2 - counted.get(j["id"], 0)
    print(f"running {len(todo)} jobs, workers={a.workers}", flush=True)
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(lambda j: run_one(j, base), todo))


if __name__ == "__main__":
    main()
