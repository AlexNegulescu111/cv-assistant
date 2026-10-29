"""Background tasks for the dashboard: one worker runs queued tasks in order, step by step, output streamed line by line.

Steps (stored as JSON):
    {"run": [argv]}                      jobpilot CLI in a child process (`python -I -u ... jobpilot <argv> --workspace <root>`)
    {"claude": prompt, "label": text}    Claude Code headless (`claude -p`, workspace as cwd → CLAUDE.md + project skills);
                                         the prompt goes in on stdin, stream-json events become readable log lines
    {"login": url}                       a visible browser window on the workspace profile (login_window.py)
    "snapshot": "<workspace key>"   on a Claude step: the file is copied first; its diff goes to the log and the
                                    result, and revert() puts the copy back while nothing else changed the file
    "then": "write"   on an `apply run` step: each `RESULT write <id>` it prints adds a Claude step for that job, then
                      one more `apply run --ids ... --after-write` (the auto-apply skill's step 3, without a person
                      in between; a job still not written then goes to needs_human)
    "then": "apply"   on an `apply add` step: the `ADDED|EXISTS <id>` it prints adds `apply run --ids <id>` with
                      "then": "write" (a link the user gives after a CV made from a pasted description)
    "soft": true      a failure is logged and the task goes on (one job's CV never stops the others)

One worker on purpose: `apply run` and `apply resume` share one persistent browser profile, which only one browser
can open at a time. Tasks and their logs are kept in the workspace db (table dashboard_tasks), so the history
survives a restart; a task left running by a crash is marked failed at the next start.
"""
import csv
import difflib
import hashlib
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from jobpilot import skill_clusters, store
from jobpilot.store import now_utc

from . import prompts

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "login_window.py")
BOOTSTRAP = "import sys; sys.path.insert(0, sys.argv.pop(1)); from jobpilot.cli import main; sys.exit(main())"
SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, title TEXT NOT NULL, steps TEXT NOT NULL,   -- steps: JSON list (see the module doc)
    status TEXT NOT NULL,                                           -- queued | running | done | failed | cancelled
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    step INTEGER, log TEXT NOT NULL DEFAULT '',
    result TEXT                                                     -- the final report of each Claude step
);
"""
USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (           -- one row per Claude run (task step, Mirel or onboarding turn)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, source TEXT NOT NULL,          -- source: task | mirel | onboarding
    task_id INTEGER, kind TEXT, label TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
    cost_usd REAL,                                   -- the CLI's API-equivalent estimate (not a bill on a plan)
    model TEXT, turns INTEGER, duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS usage_at ON usage_events(at);
"""
MIGRATIONS = {"result": "TEXT"}
FIELDS = ("id", "kind", "title", "status", "created_at", "started_at", "finished_at", "step", "result")
# Claude steps may read/write workspace files and run the project's python scripts; nothing else (no web, no shell).
CLAUDE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Skill", "TodoWrite",
                "Bash(python -I *)", "Bash(python -I:*)", "PowerShell(python -I *)", "PowerShell(python -I:*)"]
CLAUDE_DENY = ["WebFetch", "WebSearch"]
WRITE_RESULT = re.compile(r"^RESULT write\s+(\d+)\b", re.M)
ADDED_RESULT = re.compile(r"^(?:ADDED|EXISTS) (\d+)\b", re.M)
REPORT_PDF = re.compile(r"output[\\/]pdf[\\/][^\s`'\"<>|]+?\.pdf", re.I)
REPORT_JOB = re.compile(r"^JOB:\s*(.*?)\s*\|\s*(.+?)\s*$", re.M)
PASTED = re.compile(r"<job_description>\n(.*)\n</job_description>", re.S)
AUTH_ERROR = re.compile(r"authenticat|not logged in|/login|OAuth", re.I)
MAX_DESCRIPTION = 30000


def web_url(text):
    """The user's link → the URL; ValueError for anything that is not a plain http(s) address, or LinkedIn."""
    url = (text or "").strip()
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme not in ("http", "https") or not host or any(c.isspace() for c in url):
        raise ValueError("give the site's full address (https://…)")
    if any(host == d or host.endswith("." + d) for d in ("linkedin.com", "lnkd.in")):
        raise ValueError("LinkedIn is never opened")
    return url, host


def task_steps(kind, job_id=None, dry_run=False, text=None, source=None):
    """A task kind → (title, steps). apply_link: source = {pdf, company, title, description_file} from the CV task."""
    dry = ["--dry-run"] if dry_run else []
    if kind == "auto_apply":
        return ("Aplică automat" + (" (simulare)" if dry_run else ""),
                [{"run": ["collect", "run"]}, {"run": ["score", "score"]},
                 {"run": ["apply", "run"] + dry, "then": "write", "dry_run": dry_run}])
    if kind == "plan":
        return "Planul aplicărilor", [{"run": ["apply", "plan"]}]
    if kind == "collect":
        return "Colectează joburi" + (" (simulare)" if dry_run else ""), [{"run": ["collect", "run"] + dry}]
    if kind == "score":
        return "Scorează joburi", [{"run": ["score", "score"]}]
    if kind == "resume":
        return f"Formular precompletat — job {job_id}", [{"run": ["apply", "resume", str(int(job_id))]}]
    if kind == "cv":
        text = (text or "").strip()
        if len(text) < 200:
            raise ValueError("the job description is too short (paste the whole ad)")
        first = next((l.strip(" #*\t") for l in text.splitlines() if l.strip(" #*\t")), "")[:60]
        return f"CV personalizat — {first}", [{"claude": prompts.cv_from_text(text[:MAX_DESCRIPTION]),
                                               "label": "cv-for-job"}]
    if kind == "competency":
        text = (text or "").strip()
        if len(text) < 20:
            raise ValueError("describe the skill, project or result in a sentence or two")
        return (f"Competență nouă — {text.splitlines()[0][:50]}",
                [{"claude": prompts.competency(text[:MAX_DESCRIPTION]), "label": "add-competency",
                  "snapshot": "skills_pool"}])
    if kind == "onboard_extract":
        return "Onboarding — citesc CV-ul", [{"claude": prompts.onboard_extract(), "label": "onboard-extract"}]
    if kind == "onboard_write":   # source = {update} from Runner.onboard_source()
        return ("Onboarding — scriu MASTER_SKILLS_POOL.md și răspunsurile" + (" (actualizare)" if source["update"] else ""),
                [{"claude": prompts.onboard_write(source["update"]), "label": "onboard-write",
                  "snapshot": ["skills_pool", "answers_pool", "scoring", "profile"]}])
    if kind == "cluster_skills":   # source = {names, clusters, path} from Runner.cluster_source()
        return (f"Grupează skill-urile cerute — {len(source['names'])} nume noi",
                [{"claude": prompts.cluster_skills(source["names"], source["clusters"], source["path"]),
                  "label": "cluster-skills", "snapshot": "skill_clusters"}])
    if kind == "login":
        url, host = web_url(text)
        return f"Autentificare — {host}", [{"login": url}]
    if kind == "apply_link":
        url, host = web_url(text)
        if not source or not source.get("pdf"):
            raise ValueError("this CV task made no PDF")
        add = ["apply", "add", url, "--cv", source["pdf"]]
        for flag in ("title", "company", "description_file"):
            if source.get(flag):
                add += [f"--{flag.replace('_', '-')}", source[flag]]
        label = " — ".join(x for x in (source.get("company"), source.get("title")) if x) or host
        return (f"Aplică la link — {label[:60]}" + (" (simulare)" if dry_run else ""),
                [{"run": add, "then": "apply", "dry_run": dry_run}])
    raise KeyError(kind)


def describe(step):
    if "run" in step:
        return "jobpilot " + " ".join(step["run"])
    if "login" in step:
        return "fereastră de autentificare " + (urlparse(step["login"]).hostname or "")
    return "claude · " + step.get("label", "")


def claude_status():
    """→ {"installed": bool, "logged_in": bool} from `claude auth status` (no model call)."""
    exe = shutil.which("claude")
    if not exe:
        return {"installed": False, "logged_in": False}
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        return {"installed": True, "logged_in": bool(json.loads(r.stdout or "{}").get("loggedIn"))}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"installed": True, "logged_in": False}


def claude_command():
    exe = shutil.which("claude") or "claude"
    # the package's skills folder is readable too: a workspace without its own copy (onboarding) reads them there
    extra = ["--add-dir", prompts.SKILLS_DIR] if os.path.isdir(prompts.SKILLS_DIR) else []
    return [exe, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
            "--allowedTools", *CLAUDE_TOOLS, "--disallowedTools", *CLAUDE_DENY, *extra]


def claude_lines(event):
    """One stream-json event → (log lines, final result or None, error flag)."""
    kind = event.get("type")
    if kind == "assistant":
        lines = []
        for part in (event.get("message") or {}).get("content") or []:
            if part.get("type") == "text" and part.get("text", "").strip():
                lines += part["text"].strip().splitlines()
            elif part.get("type") == "tool_use":
                args = part.get("input") or {}
                detail = args.get("command") or args.get("file_path") or args.get("pattern") or args.get("skill") or ""
                lines.append(f"→ {part.get('name')} {str(detail).splitlines()[0][:160] if detail else ''}".rstrip())
        return lines, None, False
    if kind == "result":
        return [], event.get("result") or "", bool(event.get("is_error"))
    return [], None, False


def claude_usage(event):
    """A stream-json `result` event → its token counts and cost estimate (None for any other event)."""
    if event.get("type") != "result":
        return None
    u = event.get("usage") or {}
    return {"input_tokens": u.get("input_tokens") or 0, "output_tokens": u.get("output_tokens") or 0,
            "cache_read_tokens": u.get("cache_read_input_tokens") or 0,
            "cache_write_tokens": u.get("cache_creation_input_tokens") or 0,
            "cost_usd": event.get("total_cost_usd"), "model": ", ".join(sorted(event.get("modelUsage") or {})) or None,
            "turns": event.get("num_turns"), "duration_ms": event.get("duration_ms")}


def record_usage(db, source, usage, task_id=None, kind=None, label=None):
    """Saves one Claude run's usage (usage_events); a run that reported none saves nothing."""
    if not usage:
        return
    db.executescript(USAGE_SCHEMA)
    db.execute("INSERT INTO usage_events (at, source, task_id, kind, label, input_tokens, output_tokens, "
               "cache_read_tokens, cache_write_tokens, cost_usd, model, turns, duration_ms) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (now_utc(), source, task_id, kind, label, usage["input_tokens"], usage["output_tokens"],
                usage["cache_read_tokens"], usage["cache_write_tokens"], usage["cost_usd"], usage["model"],
                usage["turns"], usage["duration_ms"]))
    db.commit()


def short(n):
    n = n or 0
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


def usage_line(usage):
    """→ "tokeni: in 12.3k · out 1.1k · cache 40.0k · ~$0.21 (echivalent API)" """
    cache = usage["cache_read_tokens"] + usage["cache_write_tokens"]
    cost = f" · ~${usage['cost_usd']:.2f} (echivalent API)" if usage.get("cost_usd") is not None else ""
    return (f"tokeni: in {short(usage['input_tokens'])} · out {short(usage['output_tokens'])}"
            + (f" · cache {short(cache)}" if cache else "") + cost)


class Runner:
    def __init__(self, ws, command=None, claude=None):
        """command(argv) → the jobpilot child process argv; claude() → the Claude Code argv. Tests pass fakes."""
        self.ws = ws
        self.command = command or (lambda argv: [sys.executable, "-I", "-u", "-c", BOOTSTRAP, PACKAGE_ROOT]
                                   + argv + ["--workspace", ws.root])
        self.claude = claude or claude_command
        self.login = lambda url: [sys.executable, "-I", "-u", LOGIN_SCRIPT, "--profile", ws.path("browser_profile"), url]
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.listeners = []
        self.proc = None
        self.cancelled = set()
        self._unsaved = {}   # task id → log lines not yet in the db (it was locked)
        self.busy_ms = 30000         # how long a db write waits for a child step's lock (tests lower it)
        self.on_finish = []          # callbacks(task) after a task ends (notifications, …)
        self.before_finish = []      # callbacks(task id, kind, status) just before the end is saved (state files)
        db = self._db()
        db.executescript(SCHEMA + USAGE_SCHEMA)
        have ={r[1] for r in db.execute("PRAGMA table_info(dashboard_tasks)")}
        for col, kind in MIGRATIONS.items():
            if col not in have:
                db.execute(f"ALTER TABLE dashboard_tasks ADD COLUMN {col} {kind}")
        db.execute("UPDATE dashboard_tasks SET status = 'failed', finished_at = ?, "
                   "log = log || '\n(întrerupt: serverul a fost oprit)' WHERE status IN ('queued', 'running')", (now_utc(),))
        db.commit()
        db.close()
        self.worker = threading.Thread(target=self._work, daemon=True, name="dashboard-runner")
        self.worker.start()

    def _db(self):
        db = store.connect(self.ws.path("db"))
        db.execute(f"PRAGMA busy_timeout = {self.busy_ms}")
        return db

    # --- events -------------------------------------------------------------------------------------------------
    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def _emit(self, event):
        with self.lock:
            for q in self.listeners:
                q.put(event)

    # --- tasks --------------------------------------------------------------------------------------------------
    def submit(self, kind, job_id=None, dry_run=False, text=None, source_task=None):
        source = (self.cv_source(source_task) if kind == "apply_link" else
                  self.cluster_source() if kind == "cluster_skills" else
                  self.onboard_source(kind) if kind.startswith("onboard_") else None)
        title, steps = task_steps(kind, job_id, dry_run, text, source)
        db = self._db()
        try:
            busy = db.execute("SELECT id FROM dashboard_tasks WHERE kind = ? AND title = ? AND status IN "
                              "('queued', 'running')", (kind, title)).fetchone()
            if busy:
                return self.get(busy["id"])
            cur = db.execute("INSERT INTO dashboard_tasks (kind, title, steps, status, created_at) "
                             "VALUES (?, ?, ?, 'queued', ?)", (kind, title, json.dumps(steps), now_utc()))
            db.commit()
            task_id = cur.lastrowid
        finally:
            db.close()
        task = self.get(task_id)
        self._emit({"type": "task", "task": task})   # before the worker can pick it up, so "queued" comes first
        self.queue.put(task_id)
        return task

    def cv_source(self, task_id):
        """A finished CV task → {pdf, company, title, description_file} for apply_link; ValueError otherwise.
        The pasted description is written to a file so `apply add` stores it with the job (the write step reads it)."""
        db = self._db()
        try:
            row = db.execute("SELECT * FROM dashboard_tasks WHERE id = ? AND kind = 'cv'",
                             (int(task_id) if task_id is not None else -1,)).fetchone()
        finally:
            db.close()
        if not row or row["status"] != "done":
            raise ValueError("pick a finished CV task")
        pdfs = [p for p in REPORT_PDF.findall(row["result"] or "") if os.path.exists(self.ws.rel(p))]
        if not pdfs:
            raise ValueError("this CV task made no PDF")
        job = REPORT_JOB.findall(row["result"] or "")
        source = {"pdf": pdfs[-1].replace("\\", "/"), "company": job[-1][0] if job else None,
                  "title": job[-1][1] if job else None}
        pasted = next((PASTED.search(s.get("claude", "")) for s in self._steps(row["steps"]) if s.get("claude")), None)
        if pasted:
            path = os.path.join(os.path.dirname(self.ws.path("db")), "dashboard_manual", f"task_{row['id']}_job.md")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(pasted.group(1).strip() + "\n")
            source["description_file"] = os.path.relpath(path, self.ws.root).replace("\\", "/")
            if not source["title"]:
                source["title"] = next((l.strip(" #*\t") for l in pasted.group(1).splitlines() if l.strip(" #*\t")), "")[:80]
        return source

    def onboard_source(self, kind):
        """Checks the onboarding step before its Claude task → {update}; ValueError when a step is missing."""
        from . import onboarding
        st = onboarding.state(self.ws)["steps"]
        if not st["cv"]:
            raise ValueError("upload the CV first")
        if kind == "onboard_write":
            missing = [label for key, label in (("confirmed", "confirm the facts"), ("answers", "save the form answers"),
                                                ("enrichment", "answer or skip the guided questions")) if not st[key]]
            if missing:
                raise ValueError("first: " + ", ".join(missing))
        return {"update": os.path.exists(self.ws.path("skills_pool"))}

    def cluster_source(self):
        """The demanded-skill names skill_clusters.json has not seen yet → {names, clusters, path}; ValueError when
        there are none (nothing to send to Claude)."""
        path = self.ws.rel("demanded_skills_to_learn.csv")
        rows = []
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = [r for r in csv.DictReader(f) if (r.get("skill") or "").strip()]
        clusters = skill_clusters.load(self.ws)
        names = skill_clusters.ungrouped(rows, clusters)
        if not names:
            raise ValueError("every demanded skill is already grouped")
        return {"names": names, "clusters": clusters,
                "path": os.path.relpath(self.ws.path("skill_clusters"), self.ws.root).replace("\\", "/")}

    def get(self, task_id, log=False):
        db = self._db()
        try:
            row = db.execute("SELECT * FROM dashboard_tasks WHERE id = ?", (task_id,)).fetchone()
            usage = self._usage(db, [task_id]) if row else {}
        finally:
            db.close()
        return self._public(row, log, usage.get(task_id)) if row else None

    def list(self, limit=20):
        db = self._db()
        try:
            rows = db.execute("SELECT * FROM dashboard_tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            usage = self._usage(db, [r["id"] for r in rows])
        finally:
            db.close()
        return [self._public(r, usage=usage.get(r["id"])) for r in rows]

    @staticmethod
    def _usage(db, ids):
        """→ {task id: {tokens, cost_usd, runs}} over the task's Claude runs (tokens = in + out + cache)."""
        if not ids:
            return {}
        rows = db.execute(
            "SELECT task_id, COUNT(*) AS runs, SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) "
            "+ COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0)) AS tokens, SUM(cost_usd) AS cost_usd "
            f"FROM usage_events WHERE task_id IN ({','.join('?' * len(ids))}) GROUP BY task_id", ids)
        return {r["task_id"]: {"tokens": r["tokens"], "cost_usd": r["cost_usd"], "runs": r["runs"]} for r in rows}

    @staticmethod
    def _steps(raw):
        return [{"run": s} if isinstance(s, list) else s for s in json.loads(raw)]   # older rows: plain argv lists

    @classmethod
    def _public(cls, row, log=False, usage=None):
        task = {k: row[k] for k in FIELDS}
        task["steps"] = [describe(s) for s in cls._steps(row["steps"])]
        task["usage"] = usage   # None: no Claude run (or none that reported tokens)
        if log:
            task["log"] = row["log"]
        return task

    def cancel(self, task_id):
        task = self.get(task_id)
        if not task or task["status"] not in ("queued", "running"):
            return task
        self.cancelled.add(task_id)
        if task["status"] == "running" and self.proc:
            self.proc.terminate()
        elif task["status"] == "queued":
            self._finish(task_id, "cancelled")
        return self.get(task_id)

    def _update(self, task_id, **cols):
        db = self._db()
        try:
            db.execute(f"UPDATE dashboard_tasks SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                       (*cols.values(), task_id))
            db.commit()
        finally:
            db.close()

    def _append(self, task_id, line):
        """Adds a log line. While the db is locked by a child step the lines wait in memory and go in with the next
        write that gets through, so a busy database never stops the task (the page gets each line live anyway)."""
        self._unsaved.setdefault(task_id, []).append(line + "\n")
        db = self._db()
        try:
            db.execute("UPDATE dashboard_tasks SET log = log || ? WHERE id = ?", ("".join(self._unsaved[task_id]), task_id))
            db.commit()
            del self._unsaved[task_id]
        except sqlite3.OperationalError as e:
            print(f"dashboard log for task {task_id} kept in memory: {e}", file=sys.stderr)
        finally:
            db.close()
        self._emit({"type": "line", "task_id": task_id, "line": line})

    def _finish(self, task_id, status):
        for callback in self.before_finish:   # files the new status depends on exist before anyone can read it
            try:
                callback(task_id, self.get(task_id)["kind"], status)
            except Exception as e:
                print(f"before_finish: {e}", file=sys.stderr)
        self._update(task_id, status=status, finished_at=now_utc())
        task = self.get(task_id)
        self._emit({"type": "task", "task": task})
        for callback in self.on_finish:
            try:
                callback(task)
            except Exception as e:  # a broken callback never stops the worker
                print(f"on_finish: {e}", file=sys.stderr)

    def _work(self):
        while True:
            task_id = self.queue.get()
            task = self.get(task_id)
            if not task or task["status"] != "queued":
                continue
            self._update(task_id, status="running", started_at=now_utc())
            self._emit({"type": "task", "task": self.get(task_id)})
            try:
                status = self._steps_loop(task_id)
            except Exception as e:  # a bug in a step never kills the worker
                self._append(task_id, f"ERROR {e}")
                status = "failed"
            self.cancelled.discard(task_id)
            for attempt in range(10):  # the worker must survive a locked db, or every later task waits forever
                try:
                    if task_id in self._unsaved:
                        self._append(task_id, "")
                    self._finish(task_id, status)
                    break
                except sqlite3.OperationalError as e:
                    print(f"finishing task {task_id}: {e} (retry {attempt + 1})", file=sys.stderr)
                    time.sleep(5)

    def _steps_loop(self, task_id):
        db = self._db()
        steps = self._steps(db.execute("SELECT steps FROM dashboard_tasks WHERE id = ?", (task_id,)).fetchone()[0])
        db.close()
        results = []
        i = 0
        while i < len(steps):
            step = steps[i]
            self._update(task_id, step=i)
            self._emit({"type": "task", "task": self.get(task_id)})
            self._append(task_id, f"$ {describe(step)}")
            if "run" in step:
                code, output = self._run(task_id, self.command(step["run"]))
            elif "login" in step:
                code, output = self._run(task_id, self.login(step["login"]))
            else:
                before = self._snapshot(task_id, step.get("snapshot"))
                code, output = self._run_claude(task_id, step["claude"], step.get("label"))
                diff = self._diff(task_id, step.get("snapshot"), before)
                if output or diff:
                    results.append(output + (f"\n\n{diff}" if diff else ""))
                    self._update(task_id, result="\n\n".join(results))
            if task_id in self.cancelled:
                return "cancelled"
            if code and not step.get("soft"):
                self._append(task_id, f"(pasul s-a oprit cu codul {code})")
                return "failed"
            if code:
                self._append(task_id, f"(pasul a eșuat cu codul {code}; continui cu următorul)")
            if step.get("then") == "write" and not code:
                extra = self._write_steps(step.get("dry_run"), output)
                if extra:
                    steps[i + 1:i + 1] = extra
                    self._update(task_id, steps=json.dumps(steps))
                    self._append(task_id, f"(Claude scrie pentru {len(extra) - 1} job(uri), apoi aplică din nou)")
            if step.get("then") == "apply" and not code:
                ids = ADDED_RESULT.findall(output)
                if ids:
                    steps[i + 1:i + 1] = [{"run": ["apply", "run", "--ids", ids[-1]]
                                           + (["--dry-run"] if step.get("dry_run") else []),
                                           "then": "write", "dry_run": step.get("dry_run")}]
                    self._update(task_id, steps=json.dumps(steps))
            i += 1
        return "done"

    # --- snapshots of files a Claude step may change -------------------------------------------------------------
    def _backup(self, task_id, key):
        return os.path.join(os.path.dirname(self.ws.path("db")), "dashboard_backups",
                            f"task_{task_id}_{os.path.basename(self.ws.path(key))}")

    @staticmethod
    def _keys(snapshot):
        """A step's "snapshot": one workspace key or a list of them → list."""
        return [snapshot] if isinstance(snapshot, str) else list(snapshot or [])

    def _snapshot(self, task_id, snapshot):
        """Copies each file before the step → {key: its text, None when it did not exist}."""
        before = {}
        for key in self._keys(snapshot):
            path = self.ws.path(key)
            existed = os.path.exists(path)
            text = open(path, encoding="utf-8").read() if existed else ""
            backup = self._backup(task_id, key)
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            with open(backup, "w", encoding="utf-8") as f:
                f.write(text)
            if not existed:   # revert removes a file the step created
                open(backup + ".absent", "w").close()
            before[key] = text if existed else None
        return before

    def _diff(self, task_id, snapshot, before):
        """→ the step's changes as unified diffs ("" when nothing changed); remembers each result for revert()."""
        out = []
        for key in self._keys(snapshot):
            path, backup = self.ws.path(key), self._backup(task_id, key)
            after = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            old = before.get(key)
            name = os.path.basename(path)
            if after == old:
                for f in (backup, backup + ".absent"):
                    if os.path.exists(f):
                        os.remove(f)
                self._append(task_id, f"({name} nu s-a schimbat)")
                continue
            with open(backup + ".after", "w", encoding="utf-8") as f:
                f.write(hashlib.sha256((after or "").encode("utf-8")).hexdigest())
            diff = "\n".join(difflib.unified_diff((old or "").splitlines(), (after or "").splitlines(),
                                                  f"{name} (înainte)" if old is not None else f"{name} (nou)",
                                                  f"{name} (după)", n=1, lineterm=""))
            self._append(task_id, diff)
            out.append("```diff\n" + diff + "\n```")
        return "\n\n".join(out)

    def _reverted_keys(self, task_id):
        """The workspace keys a task's Claude steps changed and kept a copy of."""
        db = self._db()
        try:
            row = db.execute("SELECT steps FROM dashboard_tasks WHERE id = ?", (task_id,)).fetchone()
        finally:
            db.close()
        keys = [k for st in (self._steps(row["steps"]) if row else []) for k in self._keys(st.get("snapshot"))]
        return [k for k in dict.fromkeys(keys) if os.path.exists(self._backup(task_id, k) + ".after")]

    def revert(self, task_id, key=None):
        """Puts back the files a task's Claude step changed (all of them, or `key`), unless one changed again
        since; a file the step created is removed. → message; ValueError."""
        keys = [key] if key else self._reverted_keys(task_id)
        keys = [k for k in keys if os.path.exists(self._backup(task_id, k) + ".after")]
        if not keys:
            raise ValueError("this task changed nothing that can be reverted")
        for k in keys:
            backup, path = self._backup(task_id, k), self.ws.path(k)
            with open(backup + ".after", encoding="utf-8") as f:
                expected = f.read().strip()
            current = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
            if hashlib.sha256(current.encode("utf-8")).hexdigest() != expected:
                raise ValueError(f"{os.path.basename(path)} changed again after this task; revert it by hand")
        names = []
        for k in keys:
            backup, path = self._backup(task_id, k), self.ws.path(k)
            if os.path.exists(backup + ".absent"):
                if os.path.exists(path):
                    os.remove(path)
                os.remove(backup + ".absent")
            else:
                shutil.copyfile(backup, path)
            os.remove(backup + ".after")
            names.append(os.path.basename(path))
            self._append(task_id, f"(modificarea din {names[-1]} a fost anulată)")
        return ", ".join(names) + " restored"

    def _write_steps(self, dry_run, output):
        ids = list(dict.fromkeys(int(m) for m in WRITE_RESULT.findall(output)))
        if not ids:
            return []
        db = self._db()
        try:
            reasons = {r["id"]: r["reason"] for r in db.execute(
                f"SELECT id, reason FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids)}
        finally:
            db.close()
        steps = [{"claude": prompts.write_for_job(j, reasons.get(j)), "label": f"write job {j}", "soft": True}
                 for j in ids]
        # --after-write: a job Claude still could not write for leaves the rotation (needs_human), so the next
        # "Aplică automat" takes the next jobs instead of the same top-scored one again
        return steps + [{"run": ["apply", "run", "--ids", *map(str, ids), "--after-write"]
                         + (["--dry-run"] if dry_run else [])}]

    def _run(self, task_id, argv, stdin=None, parse=None, env=None):
        """Runs one child process; each stdout line goes through parse (→ log lines) → (exit code, raw output)."""
        try:
            self.proc = subprocess.Popen(argv, cwd=self.ws.root, env=env,
                                         stdin=subprocess.PIPE if stdin is not None else None,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace")
        except OSError as e:
            self._append(task_id, f"ERROR {e}")
            return 1, ""
        if stdin is not None:
            self.proc.stdin.write(stdin)
            self.proc.stdin.close()
        raw = []
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            raw.append(line)
            for shown in (parse(line) if parse else [line]):
                self._append(task_id, shown)
        code = self.proc.wait()
        self.proc = None
        return code, "\n".join(raw)

    def _run_claude(self, task_id, prompt, label=None):
        """→ (exit code, final report). A failed login is said plainly, with what to do. The run's tokens go to
        usage_events and, as one line, to the log."""
        final = {"text": "", "error": False, "usage": None}

        def parse(line):
            try:
                event = json.loads(line)
            except ValueError:
                return [line] if line.strip() else []
            lines, result, error = claude_lines(event)
            if result is not None:
                final.update(text=result, error=error, usage=claude_usage(event))
            return lines

        env = None
        key = self.ws.env("ANTHROPIC_API_KEY")   # set in Credentials: the CLI bills this key instead of the plan
        if key:
            env = {**os.environ, "ANTHROPIC_API_KEY": key}
        code, _ = self._run(task_id, self.claude(), stdin=prompt, parse=parse, env=env)
        if final["usage"]:
            task = self.get(task_id)
            db = self._db()
            try:
                record_usage(db, "onboarding" if (task["kind"] or "").startswith("onboard") else "task",
                             final["usage"], task_id, task["kind"], label)
            except sqlite3.OperationalError as e:   # the log line still shows the tokens
                print(f"usage for task {task_id} not saved: {e}", file=sys.stderr)
            finally:
                db.close()
            self._append(task_id, usage_line(final["usage"]))
        if final["error"] and AUTH_ERROR.search(final["text"]):
            self._append(task_id, "Claude CLI nu este autentificat: deschide un terminal, rulează `claude` și "
                                  "autentifică-te (/login), apoi pornește din nou.")
        if final["error"] and not code:
            code = 1
        return code, "" if final["error"] else final["text"]
