#!/usr/bin/env python3
"""Generate pages through the OpenRouter API (a separate arm, `openrouter`).

Used only for the three Neon photographer pages that Antigravity never returned
(502s, then a six-day quota). Conditions differ from the main run: no system
prompt, no tools, the bare prompt as the only message, reasoning effort "high",
every other sampling setting left at the provider default.

The key is read from the OPENROUTER_KEY environment variable and never written anywhere.
"""
import argparse, json, os, sys, time, urllib.request, urllib.error, datetime
import gen

MODEL = "google/gemini-4-neon"


def call(prompt, timeout):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "reasoning": {"effort": "high"}, "usage": {"include": True}}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_KEY"],
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), body
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode(errors="replace")[:2000]}, body
    except Exception as e:
        return {"error": repr(e)[:500]}, body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/data/scratch/ai-sameness/main")
    ap.add_argument("--ids", required=True, help="comma list of model__prompt__rN ids")
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args()
    os.makedirs(f"{a.base}/openrouter", exist_ok=True)
    for jid in a.ids.split(","):
        model, pk, run = jid.split("__")
        pid = f"{jid}__openrouter"
        page = f"{a.base}/pages/{pid}.html"
        if os.path.exists(page):
            print(pid, "already there"); continue
        for attempt in (1, 2):  # one rerun on failure, as in the main run
            started = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            t = time.time()
            resp, body = call(gen.PROMPTS[pk], a.timeout)
            secs = round(time.time() - t)
            with open(f"{a.base}/openrouter/{pid}.a{attempt}.response.json", "w") as f:
                json.dump({"request": body, "response": resp}, f, indent=1)
            text = ""
            try:
                text = resp["choices"][0]["message"]["content"] or ""
            except Exception:
                pass
            with open(f"{a.base}/openrouter/{pid}.a{attempt}.reply.txt", "w") as f:
                f.write(text)
            html, cap = gen.extract_html(text)
            ok = html is not None
            if ok:
                with open(page, "w") as f:
                    f.write(html)
            ch = (resp.get("choices") or [{}])[0] if isinstance(resp, dict) else {}
            rec = {"id": pid, "attempt": attempt, "vendor": "gemini", "model_id": MODEL,
                   "arm": "openrouter", "prompt": pk, "run": int(run[1:]), "capture": cap,
                   "reply_chars": len(text), "ok": ok, "secs": secs,
                   "via": "or_run.py (OpenRouter chat completions, no system prompt, reasoning effort high)",
                   "provider": resp.get("provider"), "served_model": resp.get("model"),
                   "finish_reason": ch.get("finish_reason"), "usage": resp.get("usage"),
                   "error": resp.get("error") or resp.get("http_error"), "started": started}
            with open(f"{a.base}/runlog.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(pid, f"a{attempt}", "ok" if ok else "FAILED", cap, len(text), f"{secs}s",
                  rec["finish_reason"], (rec["usage"] or {}).get("cost"), flush=True)
            if ok:
                break


if __name__ == "__main__":
    main()
