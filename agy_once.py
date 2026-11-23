#!/usr/bin/env python3
"""Get ONE reply from a Gemini model through Google's Antigravity ACP server,
with as little harness influence as possible.

  agy_once.py --model <slug> --prompt-file <file> --out <reply.txt> --log <events.jsonl> [--timeout 1800]
  agy_once.py --list-models --log <events.jsonl>          # start, print models/modes, exit
  agy_once.py --serve --jobs <jobs.jsonl> --log-dir <dir>  # one server, one fresh session per job

Isolation:
  * fresh server process per call (or per batch with --serve)
  * clean GEMINI_HOME (/data/scratch/ai-sameness/gemini-home) holding only a symlink to the
    signed-in token file and a minimal settings.json; brain/ and conversations/ are emptied
    before every session
  * HOME and TMPDIR point into scratch, BROWSER is /bin/false, minimal environment
  * cwd is a new empty directory per session (not in any git repo)
  * initialize advertises no client capabilities (no fs, no terminal); session/new has mcpServers: []
  * the prompt file's text is sent byte-for-byte as a single text block
  * every permission request is rejected; client fs/terminal requests get a JSON-RPC error
Every JSON-RPC message (both directions) is logged with token-like strings redacted.
Stdlib only. Never reads the token file.
"""
import argparse
import collections
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

AGY_DIR = ("/home/research/.t3/tools/antigravity-acp/linux-x64/versions/"
           "9fb60956af0a9d76220a4db91ca9ac88e2a2372ad68f985ab5fceace6b825b96")
SERVER = os.path.join(AGY_DIR, "agy_acp_server.par")
HARNESS = os.path.join(AGY_DIR, "localharness_external")
REAL_TOKEN = ("/home/research/.t3/userdata/providers/antigravity/"
              "ac0a3dfd6dddb20962cecff6ee5fe65e19d3923be20e52c5ab52ff877f7e4c32/antigravity-acp/acp_token.json")
SCRATCH = "/data/scratch/ai-sameness"
DEFAULT_HOME = os.path.join(SCRATCH, "gemini-home")
DEFAULT_WORKROOT = os.path.join(SCRATCH, "agy-work")

# ---------------------------------------------------------------- redaction
SECRET_KEY_RE = re.compile(r"(token|secret|password|passwd|authorization|credential|api[_-]?key|cookie|bearer)", re.I)
SECRET_VAL_RES = [
    re.compile(r"ya29\.[0-9A-Za-z_\-\.]+"),            # Google access tokens
    re.compile(r"1//[0-9A-Za-z_\-]{20,}"),              # Google refresh tokens
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),             # API keys
    re.compile(r"eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),  # JWTs
    re.compile(r"GOCSPX-[0-9A-Za-z_\-]+"),              # client secrets
    re.compile(r"(?i)(bearer\s+)[0-9A-Za-z_\-\.=]{16,}"),
    re.compile(r"(?i)((?:access|refresh|id)_token[\"']?\s*[:=]\s*[\"']?)[^\"'\s,&}]+"),
    re.compile(r"(?i)([?&](?:code|token|access_token)=)[^&\s\"']+"),
]


def redact_str(s):
    for r in SECRET_VAL_RES:
        if r.groups:
            s = r.sub(lambda m: m.group(1) + "[REDACTED]", s)
        else:
            s = r.sub("[REDACTED]", s)
    return s


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_KEY_RE.search(str(k)) and not isinstance(v, (dict, list, bool)) and v not in (None, ""):
                # keep counters like "outputTokens": 12 — numbers are not secrets
                out[k] = v if isinstance(v, (int, float)) else "[REDACTED]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return redact_str(obj)
    return obj


# ---------------------------------------------------------------- helpers
def stat_token():
    """mtime/inode/size of the real token file. Never reads its contents."""
    try:
        st = os.stat(REAL_TOKEN)
        return {"mtime": st.st_mtime, "ino": st.st_ino, "size": st.st_size}
    except OSError as e:
        return {"error": str(e)}


def unlink_token_copy(link):
    """The server refreshes OAuth tokens by writing a temp file next to acp_token.json and
    os.replace()-ing it over the path. With a symlink that REPLACES the link with a regular file
    in our scratch home (T3's original is untouched). Delete that copy unread, so no credential
    copy lingers in scratch; the next run re-links to T3's file."""
    if os.path.lexists(link) and not os.path.islink(link):
        os.unlink(link)
        return True
    return False


def token_link_state(home):
    link = os.path.join(home, "antigravity-acp", "acp_token.json")
    if os.path.islink(link):
        return "symlink"
    return "regular-file" if os.path.exists(link) else "missing"


def prepare_home(home):
    for d in ("antigravity-acp/brain", "antigravity-acp/conversations", "config/skills", "antigravity-cli/skills"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    acp = os.path.join(home, "antigravity-acp")
    link = os.path.join(acp, "acp_token.json")
    if os.path.islink(link):
        if os.readlink(link) != REAL_TOKEN:
            os.unlink(link)
    elif os.path.exists(link):
        unlink_token_copy(link)
    if not os.path.islink(link):
        os.symlink(REAL_TOKEN, link)
    settings = os.path.join(acp, "settings.json")
    if not os.path.exists(settings):
        with open(settings, "w") as f:
            f.write('{"auth":{"type":"oauth-personal"}}')
    for d in ("home", "tmp"):
        os.makedirs(os.path.join(home, "..", "agy-" + d), exist_ok=True)


def list_tree(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                out.append([os.path.relpath(p, root), os.path.getsize(p)])
            except OSError:
                out.append([os.path.relpath(p, root), -1])
    return sorted(out)


def wipe_state(home):
    """Empty brain/ and conversations/ so no memory can carry across sessions.
    Returns the list of files that were present (for the audit log)."""
    found = {}
    for d in ("antigravity-acp/brain", "antigravity-acp/conversations", "config/skills", "antigravity-cli/skills"):
        p = os.path.join(home, d)
        found[d] = list_tree(p)
        for e in os.listdir(p):
            q = os.path.join(p, e)
            if os.path.isdir(q) and not os.path.islink(q):
                shutil.rmtree(q)
            else:
                os.unlink(q)
    # anything else that appeared in the home (besides our two files) is reported, not deleted
    extra = [f for f in list_tree(home) if not f[0].startswith(("antigravity-acp/brain/", "antigravity-acp/conversations/"))
             and f[0] not in ("antigravity-acp/acp_token.json", "antigravity-acp/settings.json")]
    found["other"] = extra
    return found


class Log:
    def __init__(self, path):
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self.f = open(path, "a", buffering=1)
        self.t0 = time.time()
        self.lock = threading.Lock()

    def write(self, kind, payload):
        rec = {"t": round(time.time() - self.t0, 3), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "dir": kind, "msg": redact(payload)}
        with self.lock:
            self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def close(self):
        self.f.close()


class AcpError(Exception):
    pass


# ---------------------------------------------------------------- ACP client
class Server:
    def __init__(self, home, log, stderr_path, extra_args=(), extra_env=None):
        self.log = log
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "HOME": os.path.abspath(os.path.join(home, "..", "agy-home")),
            "TMPDIR": os.path.abspath(os.path.join(home, "..", "agy-tmp")),
            "GEMINI_HOME": home,
            "AGY_ACP_FORCE_FILE_STORAGE": "1",
            "ANTIGRAVITY_HARNESS_PATH": HARNESS,
            "BROWSER": "/bin/false",
            "PYTHONUNBUFFERED": "1",
        }
        env.update(extra_env or {})
        self.env_names = sorted(env)
        self.stderr_f = open(stderr_path, "a", buffering=1)
        self.t_spawn = time.time()
        self.proc = subprocess.Popen(
            [SERVER, "--uid="] + list(extra_args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=os.path.abspath(os.path.join(home, "..", "agy-tmp")),
            start_new_session=True)
        log.write("meta", {"event": "spawn", "pid": self.proc.pid, "argv": [SERVER, "--uid="] + list(extra_args),
                           "env_names": self.env_names, "GEMINI_HOME": home})
        self.q = queue.Queue()
        self.next_id = 1
        self.wlock = threading.Lock()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.on_notification = None
        self.on_request = None

    def _read_stdout(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.log.write("raw-stdout", line)
                self.q.put(("raw", line))
                continue
            self.log.write("recv", msg)
            self.q.put(("msg", msg))
        self.q.put(("eof", None))

    def _read_stderr(self):
        for raw in self.proc.stderr:
            self.stderr_f.write(redact_str(raw.decode("utf-8", "replace")))

    def send(self, msg):
        self.log.write("send", msg)
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self.wlock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method, params, deadline):
        rid = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError("timeout waiting for %s" % method)
            try:
                kind, msg = self.q.get(timeout=min(left, 5))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise AcpError("server exited rc=%s while waiting for %s" % (self.proc.returncode, method))
                continue
            if kind == "eof":
                raise AcpError("server closed stdout while waiting for %s" % method)
            if kind == "raw":
                if "accounts.google.com" in msg or "oauth" in msg.lower():
                    raise AcpError("server asked for interactive sign-in (see log)")
                continue
            if "method" in msg and "id" in msg:
                self._handle_request(msg)
            elif "method" in msg:
                if self.on_notification:
                    self.on_notification(msg)
            elif msg.get("id") == rid:
                if "error" in msg:
                    raise AcpError("%s failed: %s" % (method, json.dumps(redact(msg["error"]))[:2000]))
                return msg.get("result")

    def _handle_request(self, msg):
        result = self.on_request(msg) if self.on_request else None
        if result is None:
            self.send({"jsonrpc": "2.0", "id": msg["id"],
                       "error": {"code": -32601, "message": "method not supported by this client: %s" % msg["method"]}})
        else:
            self.send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def close(self):
        pid = self.proc.pid
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
                try:
                    os.killpg(pid, sig)   # process group of the server WE spawned (start_new_session)
                except ProcessLookupError:
                    break
                try:
                    self.proc.wait(timeout=wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
        # also reap any leftover children in our own process group (e.g. localharness_external)
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.log.write("meta", {"event": "closed", "pid": pid, "rc": self.proc.returncode})
        self.stderr_f.close()


def flatten_options(opt):
    out = []
    for e in opt.get("options") or []:
        if "value" in e:
            out.append(e)
        else:
            out.extend(e.get("options") or [])
    return out


def find_config(config_options, cid):
    for o in config_options or []:
        if o.get("id") == cid or o.get("category") == cid:
            return o
    return None


def resolve_model(slug, config_options, models_field):
    """Return (server_value, all_values). Accept exact value, case-insensitive value, or name."""
    values = []
    mc = find_config(config_options, "model")
    if mc:
        values = [(o.get("value"), o.get("name")) for o in flatten_options(mc)]
    elif models_field:
        values = [(m.get("modelId"), m.get("name")) for m in models_field.get("availableModels", [])]
    for v, n in values:
        if v == slug:
            return v, values
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    for v, n in values:
        if norm(v) == norm(slug) or norm(n) == norm(slug):
            return v, values
    return None, values


# ---------------------------------------------------------------- one session
class Session:
    def __init__(self, server, log, enabled_tools=None):
        self.s = server
        self.log = log
        self.enabled_tools = enabled_tools

    def run(self, model, prompt_text, workdir, deadline, mode=None, list_only=False):
        s = self.s
        res = {"tool_calls": [], "tool_files": [], "context_tokens": None, "permission_requests": [], "client_requests": [], "reply": [], "thoughts": [],
               "updates": collections.Counter(), "server_model": None}
        sid_holder = {}

        def on_note(msg):
            if msg.get("method") != "session/update":
                res["updates"]["notif:" + str(msg.get("method"))] += 1
                return
            p = msg.get("params") or {}
            if sid_holder.get("sid") and p.get("sessionId") not in (None, sid_holder["sid"]):
                res["updates"]["foreign-session"] += 1
            u = p.get("update") or {}
            k = u.get("sessionUpdate")
            res["updates"][k] += 1
            c = u.get("content")
            if k == "agent_message_chunk" and isinstance(c, dict):
                if c.get("type") == "text":
                    res["reply"].append(c.get("text", ""))
                else:
                    res["updates"]["non-text-message:" + str(c.get("type"))] += 1
            elif k == "agent_thought_chunk" and isinstance(c, dict):
                res["thoughts"].append(c.get("text", ""))
            elif k == "tool_call":
                res["tool_calls"].append({"id": u.get("toolCallId"), "kind": u.get("kind"), "title": u.get("title"),
                                          "status": u.get("status")})
                # file payloads the agent tried to write (create_file/edit_file): keep them verbatim
                for e in u.get("content") or []:
                    if isinstance(e, dict) and "newText" in e:
                        res["tool_files"].append({"toolCallId": u.get("toolCallId"), "path": e.get("path"),
                                                  "oldText": e.get("oldText"), "newText": e.get("newText")})
            elif k == "usage_update":
                res["context_tokens"] = u.get("used")
            elif k == "config_option_update":
                mc = find_config(u.get("configOptions"), "model")
                if mc:
                    res["server_model"] = mc.get("currentValue")

        def on_req(msg):
            m = msg.get("method")
            if m == "session/request_permission":
                p = msg.get("params") or {}
                opts = p.get("options") or []
                tc = p.get("toolCall") or {}
                pick = None
                for kind in ("reject_once", "reject_always"):
                    pick = next((o for o in opts if o.get("kind") == kind), None)
                    if pick:
                        break
                res["permission_requests"].append({"toolCallId": tc.get("toolCallId"), "title": tc.get("title"),
                                                   "kind": tc.get("kind"),
                                                   "answered": pick.get("optionId") if pick else "cancelled"})
                if pick:
                    return {"outcome": {"outcome": "selected", "optionId": pick["optionId"]}}
                return {"outcome": {"outcome": "cancelled"}}
            res["client_requests"].append(m)
            return None   # -> JSON-RPC error: we offer no fs/terminal/anything

        s.on_notification = on_note
        s.on_request = on_req

        t0 = time.time()
        params = {"cwd": workdir, "mcpServers": []}
        if self.enabled_tools is not None:
            # server-side built-in tool allowlist (tool_filter.py); [] disables every built-in tool,
            # which also disables start_subagent and with it the browser subagent
            params["_meta"] = {"agy": {"enabledTools": list(self.enabled_tools)}}
        # session/new fails transiently on a loaded host (harness websocket init timeout,
        # fetchAvailableModels SSL EOF); retry a few times before giving up
        for attempt in range(4):
            try:
                new = s.request("session/new", params, deadline)
                break
            except AcpError as e:
                if attempt == 3 or "-32603" not in str(e) or s.proc.poll() is not None:
                    raise
                self.log.write("meta", {"event": "retry_session_new", "attempt": attempt + 1, "error": str(e)[:300]})
                time.sleep(5 * (attempt + 1))
        sid = new.get("sessionId")
        sid_holder["sid"] = sid
        res["session_id"] = sid
        res["session_new"] = new
        cfg = new.get("configOptions") or []
        mc = find_config(cfg, "model")
        res["default_model"] = (mc or {}).get("currentValue") or (new.get("models") or {}).get("currentModelId")
        res["server_model"] = res["default_model"]
        res["available_models"] = resolve_model("", cfg, new.get("models"))[1]
        modec = find_config(cfg, "mode")
        res["default_mode"] = (modec or {}).get("currentValue") or (new.get("modes") or {}).get("currentModeId")
        if list_only:
            res["t_session"] = time.time() - t0
            return res

        if model and model != "default":
            value, values = resolve_model(model, cfg, new.get("models"))
            if value is None:
                raise AcpError("model %r not offered by server; available: %s" % (model, values))
            if mc:
                r = s.request("session/set_config_option", {"sessionId": sid, "configId": mc.get("id", "model"),
                                                            "value": value}, deadline)
                m2 = find_config((r or {}).get("configOptions"), "model")
                res["server_model"] = (m2 or {}).get("currentValue", value)
            else:
                s.request("session/set_model", {"sessionId": sid, "modelId": value}, deadline)
                res["server_model"] = value
        if mode:
            if modec:
                r = s.request("session/set_config_option", {"sessionId": sid, "configId": modec.get("id", "mode"),
                                                            "value": mode}, deadline)
            else:
                s.request("session/set_mode", {"sessionId": sid, "modeId": mode}, deadline)
            res["mode"] = mode
        res["t_session"] = time.time() - t0

        t1 = time.time()
        try:
            pr = s.request("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": prompt_text}]},
                           deadline)
            res["stop_reason"] = (pr or {}).get("stopReason")
            res["prompt_result"] = pr
        except TimeoutError:
            res["stop_reason"] = "client_timeout"
            try:
                s.notify("session/cancel", {"sessionId": sid})
            except Exception:
                pass
        res["t_generate"] = time.time() - t1
        return res


def summarize(res, t_total, t_start, error=None, extra=None):
    kinds = collections.Counter()
    for tc in res.get("tool_calls", []):
        kinds[(tc.get("kind") or "?") + ":" + (tc.get("title") or "?")[:60]] += 1
    reply = "".join(res.get("reply", []))
    harness_error = reply.lstrip().startswith("Agent execution error:")
    if harness_error and error is None:
        error = "harness error reply: " + reply.strip().splitlines()[0]
    out = {
        "ok": bool(reply.strip()) and error is None,
        "model": res.get("server_model"),
        "default_model": res.get("default_model"),
        "seconds": round(t_total, 1),
        "startup_s": round(t_start, 1) if t_start is not None else None,
        "session_s": round(res.get("t_session", 0), 1),
        "generate_s": round(res.get("t_generate", 0), 1),
        "reply_chars": len(reply),
        "thought_chars": len("".join(res.get("thoughts", []))),
        "tool_calls": len(res.get("tool_calls", [])),
        "tool_kinds": dict(kinds),
        "permission_requests": len(res.get("permission_requests", [])),
        "client_requests": res.get("client_requests", []),
        "stop_reason": res.get("stop_reason"),
        "context_tokens": res.get("context_tokens"),
        "tool_files": [f.get("path") for f in res.get("tool_files", [])],
    }
    if extra:
        out.update(extra)
    if error:
        out["error"] = str(error)[:500]
    return out


def start_server(args, log):
    prepare_home(args.home)
    stderr_path = (args.log if args.log else os.path.join(args.log_dir, "server")) + ".stderr"
    srv = Server(args.home, log, stderr_path)
    deadline = time.time() + args.timeout
    init = srv.request("initialize", {
        "protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        "clientInfo": {"name": "agy_once", "version": "0.1"},
    }, deadline)
    t_start = time.time() - srv.t_spawn
    methods = [m.get("id") for m in (init or {}).get("authMethods", [])]
    if "oauth-personal" in methods:
        srv.request("authenticate", {"methodId": "oauth-personal"}, deadline)
    return srv, init, t_start, deadline


def new_workdir(root, tag):
    os.makedirs(root, exist_ok=True)
    d = os.path.join(root, time.strftime("%Y%m%d-%H%M%S-") + tag + "-" + uuid.uuid4().hex[:6])
    os.makedirs(d)
    return d


def capture_after(home, res, workdir):
    """Copy the session's brain dir (artifacts, plans) next to the workdir and list files the agent wrote."""
    sid = res.get("session_id")
    extra = {"workdir": workdir, "files_in_cwd": list_tree(workdir)}
    if sid:
        b = os.path.join(home, "antigravity-acp", "brain", sid)
        if os.path.isdir(b):
            dst = workdir + ".brain"
            shutil.copytree(b, dst, symlinks=True)
            extra["brain_files"] = list_tree(dst)
            extra["brain_copy"] = dst
    return extra


READING_TOOLS = {"view_file", "list_directory", "search_directory", "find_file", "run_command"}


def warn_tools(spec):
    """view_file is auto-allowed by the server's safe_defaults policy (no permission prompt) and its
    allowed dirs include GEMINI_HOME, which holds the token symlink. Warn if such tools are on."""
    tools = parse_tools(spec)
    if tools is None or READING_TOOLS & set(tools):
        sys.stderr.write("agy_once: WARNING: --tools %s exposes file-reading tools; view_file runs without a "
                         "permission prompt and can read GEMINI_HOME (token symlink). Prefer 'none' or "
                         "'create_file'.\n" % spec)


def parse_tools(spec):
    """'none' -> [] (disable all built-ins); 'default' -> None (server defaults); else comma list."""
    if spec is None or spec == "default":
        return None
    if spec == "none":
        return []
    return [t.strip() for t in spec.split(",") if t.strip()]


def one_job(srv, log, args, model, prompt_text, out_path, deadline, t_start, t_begin):
    wipe = wipe_state(args.home)
    log.write("meta", {"event": "wiped_state_before_session", "found": wipe})
    workdir = new_workdir(args.workroot, re.sub(r"[^A-Za-z0-9]+", "-", model or "default")[:30])
    res = {}
    err = None
    try:
        res = Session(srv, log, parse_tools(args.tools)).run(model, prompt_text, workdir, deadline, mode=args.mode)
    except Exception as e:   # noqa: BLE001
        err = e
        log.write("meta", {"event": "error", "error": repr(e)})
    reply = "".join(res.get("reply", []))
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(reply)
        if res.get("thoughts"):
            with open(out_path + ".thoughts.txt", "w", encoding="utf-8") as f:
                f.write("".join(res["thoughts"]))
    if out_path and res.get("tool_files"):
        d = out_path + ".toolfiles"
        os.makedirs(d, exist_ok=True)
        for i, tf in enumerate(res["tool_files"]):
            name = "%02d-%s" % (i, os.path.basename(tf.get("path") or "file"))
            with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as f:
                f.write(tf.get("newText") or "")
        with open(os.path.join(d, "index.json"), "w") as f:
            json.dump([{k: v for k, v in tf.items() if k != "newText"} for tf in res["tool_files"]], f, indent=1)
    extra = capture_after(args.home, res, workdir)
    extra["updates"] = dict(res.get("updates", {}))
    extra["permission_detail"] = res.get("permission_requests", [])
    summ = summarize(res, time.time() - t_begin, t_start, err, extra=None)
    log.write("meta", {"event": "summary", "summary": summ, "detail": extra,
                       "tool_calls": res.get("tool_calls"), "available_models": res.get("available_models"),
                       "default_mode": res.get("default_mode")})
    return summ, err


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="default", help="server model value (or name); 'default' = do not select")
    ap.add_argument("--prompt-file")
    ap.add_argument("--out")
    ap.add_argument("--log", help="events.jsonl (single-call mode)")
    ap.add_argument("--timeout", type=float, default=1800, help="seconds for the whole call incl. startup")
    ap.add_argument("--mode", default=None, help="optional session mode value to set (default: leave server default)")
    ap.add_argument("--tools", default="create_file",
                    help="built-in tools: 'create_file' (default; only tool; every call is rejected and its payload saved), 'none' (_meta.agy.enabledTools=[]; Neon/Hydrogen then fail with malformed function call on build tasks), 'default' (server defaults), "
                         "or comma list of built-in names e.g. finish,search_web")
    ap.add_argument("--home", default=DEFAULT_HOME)
    ap.add_argument("--workroot", default=DEFAULT_WORKROOT)
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--serve", action="store_true", help="batch: one server, one fresh session per job")
    ap.add_argument("--jobs", help="jsonl lines {model, prompt_file, out[, timeout]} for --serve")
    ap.add_argument("--log-dir", help="--serve: directory for server.jsonl and per-job logs")
    args = ap.parse_args()
    args.home = os.path.abspath(args.home)
    warn_tools(args.tools)

    if args.serve:
        return serve(args)
    if not args.log:
        ap.error("--log is required")
    if not args.list_models and not (args.prompt_file and args.out):
        ap.error("--prompt-file and --out are required")

    prompt_text = None
    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8", newline="") as f:
            prompt_text = f.read()
    log = Log(args.log)
    tok_before = stat_token()
    t_begin = time.time()
    srv = None
    t_start = None
    summ = None
    err = None
    try:
        srv, init, t_start, deadline = start_server(args, log)
        if args.list_models:
            wipe_state(args.home)
            workdir = new_workdir(args.workroot, "list")
            res = Session(srv, log, parse_tools(args.tools)).run(None, None, workdir, deadline, list_only=True)
            summ = {"ok": True, "startup_s": round(t_start, 1), "agent_info": init.get("agentInfo"),
                    "default_model": res["default_model"], "default_mode": res["default_mode"],
                    "available_models": res["available_models"],
                    "agent_capabilities": init.get("agentCapabilities"),
                    "auth_methods": [m.get("id") for m in init.get("authMethods", [])]}
        else:
            summ, err = one_job(srv, log, args, args.model, prompt_text, args.out, deadline, t_start, t_begin)
    except Exception as e:   # noqa: BLE001
        err = e
        log.write("meta", {"event": "fatal", "error": repr(e)})
        summ = summarize({}, time.time() - t_begin, t_start, e)
    finally:
        if srv:
            srv.close()
    tok_after = stat_token()
    summ["token_link"] = token_link_state(args.home)
    if summ["token_link"] == "regular-file":
        unlink_token_copy(os.path.join(args.home, "antigravity-acp", "acp_token.json"))
        summ["token_refreshed_copy_deleted"] = True
    summ["token_file_changed"] = tok_before != tok_after
    log.write("meta", {"event": "token_file_stat", "changed": tok_before != tok_after})
    log.close()
    print(json.dumps(summ, ensure_ascii=False))
    return 0 if summ.get("ok") and err is None else 1


def serve(args):
    """One server process; each job gets a fresh session, fresh cwd, and wiped brain/conversations."""
    if not (args.jobs and args.log_dir):
        sys.exit("--serve needs --jobs and --log-dir")
    os.makedirs(args.log_dir, exist_ok=True)
    jobs = [json.loads(l) for l in open(args.jobs) if l.strip()]
    log = Log(os.path.join(args.log_dir, "server.jsonl"))
    args.log = None
    tok_before = stat_token()
    t_begin = time.time()
    srv, init, t_start, _ = start_server(args, log)
    rc = 0
    try:
        for i, job in enumerate(jobs):
            jt = time.time()
            with open(job["prompt_file"], "r", encoding="utf-8", newline="") as f:
                text = f.read()
            jlog = Log(os.path.join(args.log_dir, "job-%03d.jsonl" % i))
            srv.log = jlog
            summ, err = one_job(srv, jlog, args, job.get("model", "default"), text, job["out"],
                                time.time() + float(job.get("timeout", args.timeout)), t_start if i == 0 else 0, jt)
            jlog.close()
            srv.log = log
            summ["job"] = i
            print(json.dumps(summ, ensure_ascii=False), flush=True)
            if not summ.get("ok"):
                rc = 1
            if srv.proc.poll() is not None:
                print(json.dumps({"fatal": "server exited", "rc": srv.proc.returncode}), flush=True)
                return 1
    finally:
        srv.close()
        if token_link_state(args.home) == "regular-file":
            unlink_token_copy(os.path.join(args.home, "antigravity-acp", "acp_token.json"))
            log.write("meta", {"event": "deleted_refreshed_token_copy"})
        log.write("meta", {"event": "token_file_stat", "changed": tok_before != stat_token(),
                           "seconds": time.time() - t_begin})
        log.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
