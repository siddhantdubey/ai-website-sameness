# Run log

Computed by report.py from `<scratch>/main/runlog.jsonl` (431 records), `agy_log.jsonl` (172 records) and the raw replies. All times UTC.

## CLIs, models and settings

| Model | exact model id | CLI | reasoning | model reported by the CLI (Claude: modelUsage keys) |
|---|---|---|---|---|
| Claude Fable 5.2 | `claude-fable-5-2` | Claude Code 2.1.325 | CLI default (not set) | `claude-haiku-5-5` x31, `claude-fable-5-2` x31 |
| Claude Opus 5.5 | `claude-opus-5-5` | Claude Code 2.1.325 | CLI default (not set) | `claude-haiku-5-5` x36, `claude-opus-5-5` x36 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | Claude Code 2.1.325 | CLI default (not set) | `claude-haiku-5-5` x29, `claude-sonnet-5-5` x29 |
| Claude Haiku 5.5 | `claude-haiku-5-5` | Claude Code 2.1.325 | CLI default (not set) | `claude-haiku-5-5` x30 |
| GPT-6.2 Sol | `gpt-6.2-sol` | Codex CLI 0.171.0 | Codex catalog default: low | – |
| GPT-6.2 Astra | `gpt-6.2-astra` | Codex CLI 0.171.0 | Codex catalog default: low | – |
| GPT-6.1 Sol | `gpt-6.1-sol` | Codex CLI 0.171.0 | Codex catalog default: low | – |
| GPT-6.1 Terra | `gpt-6.1-terra` | Codex CLI 0.171.0 | Codex catalog default: low | – |
| GPT-6 Luna | `gpt-6-luna` | Codex CLI 0.171.0 | Codex catalog default: medium | – |
| Gemini 4 Neon | `gemini-4-neon-high` | Antigravity ACP server 1.3.0 | High (part of the model id) | – |
| Gemini 4 Argon | `gemini-4-argon-high` | Antigravity ACP server 1.3.0 | High (part of the model id) | – |
| Gemini 4 Hydrogen | `gemini-4-hydrogen-high` | Antigravity ACP server 1.3.0 | High (part of the model id) | – |

Temperature cannot be set in any of the three CLIs and was not set.

Claude Code also made a small `claude-haiku-5-5` call inside the Fable, Opus and Sonnet sessions (found in 96 raw session records; 16-25 output tokens each, median 16). This is the CLI's own side request, not the page; `usage.output_tokens` in the run log is the main model's count only. (For Haiku sessions the two cannot be told apart.)

Model reported by the Antigravity server in each Gemini call summary (`agy/*.summary.json`, main run, including usage-limit calls): `gemini-4-argon-high` x53, `gemini-4-hydrogen-high` x60, `gemini-4-neon-high` x59.

### Exact command lines

Claude Code and Codex: as logged in `runlog.jsonl` (`cmd`, prompt removed; it is passed as the argument after `-p` for Claude and as the last argument for Codex; work-folder paths replaced by `<workdir>`).

- Claude Code 2.1.325, 63 attempts, 2026-11-23T19:41:35Z to 2026-11-24T00:23:42Z:
  `claude -p --model <model> --setting-sources '' --strict-mcp-config --tools '' --disable-slash-commands --no-session-persistence --output-format json`
- Claude Code 2.1.325, 67 attempts, 2026-11-24T00:42:31Z to 2026-11-24T03:14:37Z:
  `claude -p --model <model> --setting-sources '' --strict-mcp-config --tools '' --disable-slash-commands --no-session-persistence --output-format stream-json --verbose`
- Codex CLI 0.171.0, 127 attempts, 2026-11-23T19:41:35Z to 2026-11-24T01:46:56Z:
  `codex exec --skip-git-repo-check --ephemeral --ignore-user-config --ignore-rules -s read-only -m <model> -c 'web_search="disabled"' --disable apps --disable plugins --disable remote_plugin --json -o <workdir>/last.txt`
  - Codex environment: `CODEX_HOME=<scratch>/codex-home` (clean; only a link to the login file).
  - Claude: the `--output-format json` form is capture v1 (only the final text block was read); `--output-format stream-json --verbose` is capture v2 (every text block). See incident (a).
- Antigravity (Gemini), main run, from `agy_run.py` (one fresh server per call):
  `python3 agy_once.py --model <model> --prompt-file agy/<P>.txt --out agy/<id>.a<n>.reply.txt --log agy/<id>.a<n>.events.jsonl --timeout 1800 --tools create_file`
  then `python3 gen.py ingest --name main --id <id> --file <first create_file payload, else the reply text>`. The pilot Gemini pages (P1 run 1) were made with the same client (`agy_once.py`). Note: `gen.py ingest` writes `"via": "t3 delegate_task"` on every Gemini record; that field is a fixed string and does not describe how the page was made.

## Run dates

- Claude Code 2.1.325: first record 2026-11-23T19:41:35Z, last record 2026-11-24T03:14:37Z.
- Codex CLI 0.171.0: first record 2026-11-23T19:41:35Z, last record 2026-11-24T01:46:56Z.
- Antigravity ACP server 1.3.0: first record 2026-11-23T21:08:58Z, last record 2026-11-24T12:52:17Z (Gemini `started` is the time the reply was ingested).

## Per model

Counting rule (the one `gen.py` and `agy_run.py` apply): every record in `runlog.jsonl` is an attempt, except (a) Claude records that failed under capture v1 and (b) Gemini replies that were only the account's usage-limit notice. "First attempt failed" = the page's first counted attempt produced no page. Output tokens = sum of `usage.output_tokens` over all records as reported by each CLI (Claude includes thinking tokens, Codex includes reasoning tokens; the two are not directly comparable; the Antigravity server reports no output-token count). Wall time = sum of `seconds` over the counted attempts (Gemini: `agy_log.jsonl` and the pilot call summaries; usage-limit calls and the waits after them excluded).

| Model | pages on disk | rows in CSV | records in log | not counted | counted attempts | first attempt failed | output tokens (all records) | median output tokens per page | wall time | median s per call |
|---|---|---|---|---|---|---|---|---|---|---|
| Claude Fable 5.2 | 25 of 25 | 25 | 31 | 3 | 28 | 3 of 25 | 748,370 | 25,841 | 1.75 h (28 calls) | 217 s |
| Claude Opus 5.5 | 25 of 25 | 25 | 40 | 15 | 25 | 0 of 25 | 949,592 | 18,922 | 2.92 h (25 calls) | 177 s |
| Claude Sonnet 5.5 | 25 of 25 | 25 | 29 | 4 | 25 | 0 of 25 | 1,005,934 | 10,421 | 1.07 h (25 calls) | 71 s |
| Claude Haiku 5.5 | 25 of 25 | 25 | 30 | 5 | 25 | 0 of 25 | 1,560,878 | 12,963 | 0.97 h (25 calls) | 52 s |
| GPT-6.2 Sol | 25 of 25 | 25 | 25 | 0 | 25 | 0 of 25 | 106,034 | 4,386 | 0.63 h (25 calls) | 91 s |
| GPT-6.2 Astra | 25 of 25 | 25 | 25 | 0 | 25 | 0 of 25 | 90,708 | 3,576 | 0.56 h (25 calls) | 81 s |
| GPT-6.1 Sol | 25 of 25 | 25 | 25 | 0 | 25 | 0 of 25 | 171,304 | 6,969 | 0.98 h (25 calls) | 143 s |
| GPT-6.1 Terra | 25 of 25 | 25 | 25 | 0 | 25 | 0 of 25 | 157,571 | 6,313 | 0.85 h (25 calls) | 120 s |
| GPT-6 Luna | 25 of 25 | 25 | 27 | 0 | 27 | 2 of 25 | 179,506 | 6,954 | 0.98 h (27 calls) | 130 s |
| Gemini 4 Neon | 22 of 25 | 22 | 60 | 32 | 28 | 3 of 25 | not reported | – | 7.04 h (28 calls) | 676 s |
| Gemini 4 Argon | 25 of 25 | 25 | 53 | 28 | 25 | 0 of 25 | not reported | – | 2.15 h (25 calls) | 289 s |
| Gemini 4 Hydrogen | 25 of 25 | 25 | 61 | 36 | 25 | 0 of 25 | not reported | – | 1.64 h (25 calls) | 232 s |

Codex attempts in which the model ran a shell command in the read-only sandbox before answering: `gpt-6.2-sol` 1 of 25, `gpt-6.2-astra` 0 of 25, `gpt-6.1-sol` 1 of 25, `gpt-6.1-terra` 5 of 25, `gpt-6-luna` 26 of 27.

Gemini pages ingested by `agy_run.py`, by what was kept: first create_file payload 69.

## Every failed attempt

| id | attempt | started | reason | reply chars | output tokens | reply begins |
|---|---|---|---|---|---|---|
| opus-5.5__P1__r1 | 1 | 2026-11-23T19:50:55Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1908 | 18,383 | I created **`index.html`**, a single self-contained file with no external fonts, images, or scripts. The HTML structure checks out clean. I … |
| fable-5.2__P1__r1 | 1 | 2026-11-23T19:52:06Z | reply contained no HTML document; capture v1 (final text block only), not counted | 2379 | 35,274 | <invoke name="Bash"> <parameter name="command">cd <scratch>/pilot/work/fable-5.2__P1__r1.a1.1ia8ya8l && python3 - <<'EOF' p=… |
| sonnet-5.5__P1__r1 | 1 | 2026-11-23T20:03:28Z | reply contained no HTML document; capture v1 (final text block only), not counted | 325 | 128,258 | <invoke name="Bash"> <parameter name="command">cd <scratch>/pilot/work/sonnet-5.5__P1__r1.a1.p8ctdk6r && ls -la && wc -c ind… |
| opus-5.5__P4__r3 | 1 | 2026-11-23T21:56:42Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1667 | 12,130 | I've written the page to `index.html`. It's one HTML file with no external fonts, images or scripts, so it works offline. **The firm:** "Hol… |
| opus-5.5__P4__r3 | 2 | 2026-11-23T21:58:41Z | reply contained no HTML document; capture v1 (final text block only), not counted | 2165 | 19,727 | I've built the landing page as a single self-contained HTML file: `index.html`. It uses no external fonts, images or scripts, so it works of… |
| sonnet-5.5__P3__r2 | 1 | 2026-11-23T22:00:33Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1516 | 11,357 | The portfolio is written to `portfolio.html`, a single file of about 20 KB with no external dependencies. I haven't opened it in a browser, … |
| sonnet-5.5__P5__r5 | 1 | 2026-11-23T22:09:44Z | reply contained no HTML document; capture v1 (final text block only), not counted | 697 | 128,400 | <invoke name="Bash"> <parameter name="command">cd <scratch>/main/work/sonnet-5.5__P5__r5.a1.q84tacob && ls -la index.html &&… |
| haiku-5.5__P3__r5 | 1 | 2026-11-23T22:20:23Z | reply contained no HTML document; capture v1 (final text block only), not counted | 3404 | 129,881 | <invoke:Write> <parameter name="file_path"><scratch>/main/work/haiku-5.5__P3__r5.a1.r6u58x0c/index.html</parameter> <paramet… |
| sonnet-5.5__P1__r3 | 1 | 2026-11-23T22:29:54Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1322 | 8,485 | I left a typo in one hover color, so I'm fixing it. <invoke name="Edit"> <parameter name="file_path"><scratch>/main/work/son… |
| haiku-5.5__P4__r5 | 1 | 2026-11-23T22:33:32Z | reply contained no HTML document; capture v1 (final text block only), not counted | 443 | 128,336 | <invoke name="Bash"> <parameter name="command">cd <scratch>/main/work/haiku-5.5__P4__r5.a1.180yabwg/ && ls -la && (test -f i… |
| haiku-5.5__P4__r5 | 2 | 2026-11-23T22:40:18Z | reply contained no HTML document; capture v1 (final text block only), not counted | 589 | 128,419 | <parameter name="Bash"> <parameter name="command">cd <scratch>/main/work/haiku-5.5__P4__r5.a2.mfmefpvw && ls -la && wc -c in… |
| opus-5.5__P3__r1 | 1 | 2026-11-23T22:47:05Z | reply contained no HTML document; capture v1 (final text block only), not counted | 2076 | 19,158 | I've built the portfolio as a single self-contained file: `index.html`. It needs no external images, fonts or libraries and works offline. T… |
| opus-5.5__P3__r1 | 2 | 2026-11-23T22:49:37Z | reply contained no HTML document; capture v1 (final text block only), not counted | 2084 | 28,833 | I've built the portfolio site as a single file, `index.html`, for a fictional photographer called **Ines Varga**. It uses no external fonts,… |
| fable-5.2__P1__r2 | 1 | 2026-11-23T22:54:51Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1448 | 34,459 | The landing page is at `index.html` in the working directory: one file, no external fonts, scripts or images. I syntax-checked the script bu… |
| opus-5.5__P4__r1 | 1 | 2026-11-23T22:57:15Z | timed out after 1500.2 s with no output; capture v1 (final text block only), not counted | 0 | – |  |
| haiku-5.5__P3__r3 | 1 | 2026-11-23T23:14:51Z | reply contained no HTML document; capture v1 (final text block only), not counted | 72 | 3,231 | <invoke-tool> Write </invoke-tool> <invoke-tool> Write </invoke-tool> |
| haiku-5.5__P3__r3 | 2 | 2026-11-23T23:15:06Z | reply contained no HTML document; capture v1 (final text block only), not counted | 242 | 256,165 | I'll write the portfolio as one self-contained file, `portfolio.html`, with inline CSS and JavaScript and no external dependencies. Photos a… |
| opus-5.5__P4__r1 | 2 | 2026-11-23T23:22:15Z | reply contained no HTML document; exit code 1; capture v1 (final text block only), not counted | 451 | 63,140 | API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup). This sometimes happens with safe, normal conver… |
| opus-5.5__P1__r5 | 1 | 2026-11-23T23:37:43Z | reply contained no HTML document; capture v1 (final text block only), not counted | 2299 | 19,794 | <invoke name="Edit"> <parameter name="file_path"><scratch>/main/work/opus-5.5__P1__r5.a1.agygw99b/index.html</parameter> <pa… |
| gpt-6-luna__P4__r2 | 1 | 2026-11-23T23:45:05Z | reply contained no HTML document | 193 | 7,254 | I couldn’t create the HTML file because this workspace is read-only, and the environment blocks write requests. No file was created. The pag… items: agent_message,command_execution,agent_message,agent_message |
| opus-5.5__P3__r5 | 1 | 2026-11-23T23:45:25Z | reply contained no HTML document; capture v1 (final text block only), not counted | 2022 | 24,248 | The portfolio site is saved as **`index.html`**, a single 38 KB file with no external fonts, images or scripts. The JavaScript passes a synt… |
| opus-5.5__P5__r2 | 1 | 2026-11-23T23:56:27Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1343 | 11,858 | I've written the landing page to `index.html`. It's one file with no external dependencies, including fonts. **The product:** "Docket," a pr… |
| opus-5.5__P5__r2 | 2 | 2026-11-23T23:58:12Z | reply contained no HTML document; capture v1 (final text block only), not counted | 2462 | 12,498 | <invoke name="Edit"> <parameter name="file_path"><scratch>/main/work/opus-5.5__P5__r2.a2.ezebfomc/index.html</parameter> <pa… |
| opus-5.5__P2__r4 | 1 | 2026-11-24T00:01:29Z | reply contained no HTML document; capture v1 (final text block only), not counted | 2258 | 28,774 | I've built the landing page as a single self-contained HTML file at `index.html`. It has no external fonts, images or scripts; all the artwo… |
| opus-5.5__P2__r4 | 2 | 2026-11-24T00:05:34Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1699 | 17,300 | I built the landing page as a single file: `index.html` (about 38 KB). It loads no external fonts, images or scripts; the illustrations are … |
| fable-5.2__P1__r3 | 1 | 2026-11-24T00:18:45Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1427 | 23,867 | The landing page is in `index.html` in the working directory: one file, no external fonts, images or scripts. I couldn't open it in a browse… |
| opus-5.5__P3__r2 | 1 | 2026-11-24T00:20:14Z | reply contained no HTML document; capture v1 (final text block only), not counted | 3096 | 18,761 | <invoke name="Bash"> <parameter name="command">cd <scratch>/main/work/opus-5.5__P3__r2.a1.n5z9rf4b && sed -i "s/\${form.name… |
| opus-5.5__P3__r2 | 2 | 2026-11-24T00:23:42Z | reply contained no HTML document; capture v1 (final text block only), not counted | 1813 | 22,581 | I built the site as one HTML file with no external dependencies: no CDNs, web fonts or image links: **`index.html`** (~48 KB). The JavaScrip… |
| gpt-6-luna__P5__r1 | 1 | 2026-11-24T00:44:33Z | reply contained no HTML document | 150 | 5,583 | I can’t create the HTML file because this workspace is read-only, and writes are blocked by the current sandbox settings. I haven’t changed … items: agent_message,command_execution,command_execution,agent_message,agent_message |
| fable-5.2__P4__r4 | 1 | 2026-11-24T01:43:29Z | reply contained no HTML document | 16 | 3,185 | _cnt_placeholder |
| fable-5.2__P1__r5 | 1 | 2026-11-24T01:47:55Z | reply contained no HTML document | 925 | 7,676 | I wrote the landing page to `index.html` in the working directory; I have not opened it in a browser, so the layout and animations are unche… |
| fable-5.2__P4__r1 | 1 | 2026-11-24T03:07:46Z | reply contained no HTML document | 1485 | 5,559 | I wrote the landing page to `index.html` in the working directory: one file, no external fonts, images or scripts. I have not opened it in a… |
| neon__P3__r1 | 3 | 2026-11-24T06:09:10Z | empty reply | 0 | – |  |
| neon__P3__r1 | 4 | 2026-11-24T06:39:16Z | empty reply | 0 | – |  |
| neon__P3__r2 | 3 | 2026-11-24T08:17:42Z | empty reply | 0 | – |  |
| neon__P3__r2 | 4 | 2026-11-24T08:47:48Z | empty reply | 0 | – |  |
| neon__P3__r4 | 3 | 2026-11-24T12:05:44Z | empty reply | 0 | – |  |
| neon__P3__r4 | 4 | 2026-11-24T12:35:50Z | empty reply | 0 | – |  |

Gemini usage-limit notices saved as replies (not counted as attempts): 96 records for 48 pages, ingested 2026-11-24T00:49:03Z to 2026-11-24T01:03:56Z. Pages: argon__P1__r3, argon__P1__r4, argon__P2__r1, argon__P2__r2, argon__P2__r3, argon__P2__r4, argon__P3__r2, argon__P3__r3, argon__P4__r1, argon__P4__r2, argon__P5__r2, argon__P5__r3, argon__P5__r4, argon__P5__r5, hydrogen__P1__r2, hydrogen__P1__r3, hydrogen__P1__r4, hydrogen__P1__r5, hydrogen__P2__r1, hydrogen__P2__r3, hydrogen__P2__r5, hydrogen__P3__r1, hydrogen__P3__r2, hydrogen__P3__r3, hydrogen__P3__r4, hydrogen__P3__r5, hydrogen__P4__r1, hydrogen__P4__r2, hydrogen__P4__r3, hydrogen__P4__r4, hydrogen__P5__r3, hydrogen__P5__r4, neon__P1__r2, neon__P1__r3, neon__P1__r4, neon__P1__r5, neon__P2__r1, neon__P2__r3, neon__P2__r5, neon__P3__r1, neon__P3__r2, neon__P3__r4, neon__P4__r1, neon__P4__r2, neon__P4__r4, neon__P4__r5, neon__P5__r1, neon__P5__r3.

Usage-limit calls caught by the current `agy_run.py` before ingest (logged in `agy_log.jsonl` with `quota: true`, never saved as replies): 1, for 1 pages, total wait 2.2 h.

## Incidents

**(a) Claude capture v1.** Until capture v2 (first v2 record: 2026-11-24T00:42:31Z), the runner read only the final text block of a Claude reply (`--output-format json`, `result`). With no tools available, a model that wrote the page inside a pretend file-write and then ended with a short note left no HTML in that final block, so the attempt was logged as failed. From the logs: 27 Claude attempts failed under v1 (3 of them in the pilot) across 19 pages; by model: `claude-opus-5-5` 15, `claude-haiku-5-5` 5, `claude-sonnet-5-5` 4, `claude-fable-5-2` 3. Their saved replies were short (median 1,667 characters, max 3,404) while the CLI reported a median of 23,224 output tokens for them (min 3,231), consistent with a page having been written and not kept (the v1 output does not contain the earlier text blocks, so this cannot be confirmed from the saved files). What the saved final blocks of the exit-0 attempts contain: prose saying an .html file was written 13, written-out tool call 12. These attempts were not counted against the two-attempt limit. Pages that had failed twice under v1 and were generated again under v2: 8 (haiku-5.5__P3__r3, haiku-5.5__P4__r5, opus-5.5__P2__r4, opus-5.5__P3__r1, opus-5.5__P3__r2, opus-5.5__P4__r1, opus-5.5__P4__r3, opus-5.5__P5__r2). Pages that failed once under v1 and had already succeeded on the rerun, and were kept: 11 (fable-5.2__P1__r1, fable-5.2__P1__r2, fable-5.2__P1__r3, haiku-5.5__P3__r5, opus-5.5__P1__r1, opus-5.5__P1__r5, opus-5.5__P3__r5, sonnet-5.5__P1__r1, sonnet-5.5__P1__r3, sonnet-5.5__P3__r2, sonnet-5.5__P5__r5).

Not every excluded v1 failure was a capture problem: timeout with no output: opus-5.5__P4__r1 a1; non-zero exit: opus-5.5__P4__r1 a2 (exit 1). The rule excludes all Claude v1 failures, so these were not counted either.

Claude attempts that failed under capture v2 (counted normally): 3 (fable-5.2__P4__r4 a1, fable-5.2__P1__r5 a1, fable-5.2__P4__r1 a1).

**(b) Gemini usage limit.** The account hit its usage limit. 48 pages got usage-limit notices ("Usage Limit Reached") saved as replies: 2 notices for 48 pages; by model: `gemini-4-hydrogen-high` 36, `gemini-4-neon-high` 32, `gemini-4-argon-high` 28. The notice never reached the model, so these were not counted as attempts (`agy_run.py` since commit 42d6189), and the pages were retried after the limit reset. Of the 48: 45 have a page now, 3 do not yet (neon__P3__r1, neon__P3__r2, neon__P3__r4).

**(d) Pages re-extracted from their saved reply.** 8 page files were replaced by a second extraction from the same saved reply. No new generation was made and the reply files were not changed. The earlier page files are kept in `pages_escaped_v1/` in the run folder. Two causes: the model wrote its page inside a written-out tool call as a JSON string, which the first capture saved still escaped (capture `json-string` decodes it); or the saved file ran past the first closing html tag into written-out tool-call text (capture `bare` now stops at the first closing tag).

| id | reply file | capture | note |
|---|---|---|---|
| fable-5.2__P1__r4 | fable-5.2__P1__r4.a1.reply.txt | json-string | page was saved JSON-escaped; decoded from the same reply, no new generation |
| fable-5.2__P1__r5 | fable-5.2__P1__r5.a2.reply.txt | json-string | page was saved JSON-escaped; decoded from the same reply, no new generation |
| haiku-5.5__P2__r2 | haiku-5.5__P2__r2.a1.reply.txt | json-string | page was saved JSON-escaped; decoded from the same reply, no new generation |
| haiku-5.5__P3__r1 | haiku-5.5__P3__r1.a1.reply.txt | json-string | page was saved JSON-escaped; decoded from the same reply, no new generation |
| haiku-5.5__P3__r5 | haiku-5.5__P3__r5.a2.reply.txt | json-string | page was saved JSON-escaped; decoded from the same reply, no new generation |
| haiku-5.5__P5__r4 | haiku-5.5__P5__r4.a1.reply.txt | json-string | page was saved JSON-escaped; decoded from the same reply, no new generation |
| sonnet-5.5__P1__r3 | sonnet-5.5__P1__r3.a2.reply.txt | bare | saved page ran past the closing html tag into pretend tool-call text; cut at the first closing tag, same reply, no new generation |
| sonnet-5.5__P5__r2 | sonnet-5.5__P5__r2.a1.reply.txt | bare | saved page ran past the closing html tag into pretend tool-call text; cut at the first closing tag, same reply, no new generation |

## Could not be disabled (copied from README, "Keeping the harness out of it")


**Claude Code**

- Could not disable, even with `--system-prompt`: a one-line identity sentence ("a Claude agent built on Anthropic's Claude Agent SDK"), an environment block (working folder, OS, date, model name, plus some file-safety advice), and a note carrying the account email.

**Codex CLI**

- Could not disable: the shell tool (read-only sandbox; 3 of the 10 pilot runs executed commands before answering), the multi-agent tools and their instructions, and the built-in skills list.

**Antigravity (Gemini)**

- Could not disable: the system prompt. There is no setting, flag or protocol field for it, so there is no minimal arm for Gemini. Asked to describe it, the model reported a coding-agent identity ("Antigravity"), formatting rules and an artifact system, and nothing about how to design web pages.
- Could not disable: the prompt is wrapped in request tags with the current local time appended.

