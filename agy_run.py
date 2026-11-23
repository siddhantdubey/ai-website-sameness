#!/usr/bin/env python3
"""Run the Gemini jobs of a plan through agy_once.py, one fresh Antigravity server per page.

Kept output: the first file the model tried to create (create_file is the only tool and
every call is rejected), or the reply text if it never tried to create a file.
A failed page gets one rerun in a fresh session.

  agy_run.py --name main
"""
import argparse, json, subprocess, sys, time
from pathlib import Path
import gen

ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True)
a = ap.parse_args()
base = gen.ROOT / a.name
agy = base / "agy"
agy.mkdir(parents=True, exist_ok=True)
here = Path(__file__).resolve().parent
for k, v in gen.PROMPTS.items():
    (agy / f"{k}.txt").write_text(v)  # exact prompt text, no trailing newline

jobs = [json.loads(l) for l in (base / "jobs.jsonl").read_text().splitlines()]
jobs = [j for j in jobs if gen.MODELS[j["model"]][0] == "gemini"]
for j in jobs:
    jid = j["id"]
    for attempt in (1, 2):
        if (base / "pages" / f"{jid}.html").exists():
            break
        if len(list((base / "raw").glob(f"{jid}.a*.reply.txt"))) >= 2:
            break
        tag = f"{jid}.a{attempt}"
        out = agy / f"{tag}.reply.txt"
        t0 = time.time()
        p = subprocess.run([sys.executable, str(here / "agy_once.py"), "--model", gen.MODELS[j["model"]][1],
                            "--prompt-file", str(agy / f"{j['prompt']}.txt"), "--out", str(out),
                            "--log", str(agy / f"{tag}.events.jsonl"), "--timeout", "1800",
                            "--tools", "create_file"], capture_output=True, text=True)
        (agy / f"{tag}.summary.json").write_text(p.stdout)
        (agy / f"{tag}.err").write_text(p.stderr)
        tool = sorted(Path(str(out) + ".toolfiles").glob("*.html")) if Path(str(out) + ".toolfiles").exists() else []
        if tool:
            src, kept = tool[0], "first create_file payload"
        elif out.exists() and out.stat().st_size:
            src, kept = out, "reply text"
        else:
            src = agy / f"{tag}.empty"
            src.write_text("")
            kept = "nothing"
        subprocess.run([sys.executable, str(here / "gen.py"), "ingest", "--name", a.name, "--id", jid,
                        "--file", str(src)], capture_output=True, text=True)
        ok = (base / "pages" / f"{jid}.html").exists()
        with open(base / "agy_log.jsonl", "a") as f:
            f.write(json.dumps({"id": jid, "attempt": attempt, "exit": p.returncode, "kept": kept, "ok": ok,
                                "seconds": round(time.time() - t0, 1), "summary": p.stdout.strip()[:1500]}) + "\n")
        print(f"[{time.strftime('%H:%M:%S')}] {tag} ok={ok} kept={kept} {round(time.time()-t0)}s", flush=True)
