"""Mirel: the dashboard chat where the user answers the form questions the answers pool could not answer.

Each message is one headless Claude Code turn (`claude -p --resume <session>`, workspace as cwd, add-answer rules
in the system prompt), run on its own thread so the chat answers while the task queue works. Every turn gets the
open questions fresh: ask / unknown / no_option items of all application folders, grouped by label.

After each reply the dashboard re-matches every waiting form from the pool (`jobpilot answers`, no AI) and moves a
needs_human job back to form_ready when questions were all that held it (no CAPTCHA, login or do_not_answer left),
so the next auto-apply run takes it. Messages and the session id are kept in the workspace db.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import threading

from jobpilot import answers as answers_mod
from jobpilot import pipeline
from jobpilot.store import now_utc

from . import prompts
from .runner import claude_command, claude_usage, record_usage

QUESTION_REASONS = ("ask", "unknown", "no_option")
SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'mirel'                -- mirel | onboarding: which chat the message belongs to
);
CREATE TABLE IF NOT EXISTS dashboard_kv (key TEXT PRIMARY KEY, value TEXT);
"""
ONLY_QUESTIONS = re.compile(r"^(unknown|ask|no_option): ")


def _norm(label):
    return re.sub(r"\s+", " ", (label or "").strip().rstrip("*").strip()).lower()


def open_questions(ws, db):
    """→ [{label, reason, type, options, required, applications: [{application, job_id, company, title, field_id}]}]"""
    jobs = {r["application"]: r for r in db.execute(
        "SELECT id, company, title, application, status FROM jobs WHERE application IS NOT NULL")}
    applied = {r.get("application") for r in pipeline.applied_rows(ws.path("applied_log"))}
    base = ws.path("applications_dir")
    groups = {}
    for stem in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        path = os.path.join(base, stem, "pending.json")
        job = jobs.get(stem)
        if not os.path.exists(path) or stem in applied or (job and job["status"] in ("applied", "skipped")):
            continue
        with open(path, encoding="utf-8") as f:
            pending = json.load(f).get("pending", [])
        for p in pending:
            if p.get("reason") not in QUESTION_REASONS:
                continue
            g = groups.setdefault(_norm(p.get("label")), {
                "label": (p.get("label") or "").strip(), "reason": p["reason"], "type": p.get("type"),
                "options": p.get("options") or None, "required": False, "applications": []})
            g["required"] = g["required"] or bool(p.get("required"))
            g["applications"].append({"application": stem, "field_id": p.get("field_id"),
                                      "job_id": job["id"] if job else None,
                                      "company": job["company"] if job else None,
                                      "title": job["title"] if job else None})
    return sorted(groups.values(), key=lambda g: (not g["required"], -len(g["applications"]), g["label"]))


def rematch(ws, db, stems):
    """Re-answers these application folders from the pool, then unblocks jobs that only waited on questions.
    → [(job id, from, to)]"""
    base = ws.path("applications_dir")
    for stem in stems:
        form = os.path.join(base, stem, "form.json")
        if os.path.exists(form):
            with contextlib.redirect_stdout(io.StringIO()):
                answers_mod.main([form], ws=ws)
    moved = []
    for job in db.execute("SELECT id, application, reason FROM jobs WHERE status = 'needs_human' "
                          "AND application IS NOT NULL").fetchall():
        parts = [p for p in (job["reason"] or "").split(" → ")[0].split("; ") if p]
        if job["application"] not in stems or not parts or not all(ONLY_QUESTIONS.match(p) for p in parts):
            continue
        path = os.path.join(base, job["application"], "pending.json")
        with open(path, encoding="utf-8") as f:
            left = [p for p in json.load(f).get("pending", [])
                    if p.get("required") and p.get("reason") in QUESTION_REASONS + ("do_not_answer",)]
        if not left:
            pipeline.transition(db, job["id"], "form_ready", "answers complete (Mirel)")
            moved.append((job["id"], "needs_human", "form_ready"))
    db.commit()
    return moved


class Mirel:
    """A chat kept in the workspace db: one headless Claude Code turn per message, resumed by session id.
    Subclasses (OnboardingGuide) change the channel, the system prompt and what each turn gets as context."""
    CHANNEL = "mirel"          # dashboard_chat.channel, and the "channel" of its events
    SESSION_KEY = "mirel_session"
    SOURCE = "mirel"           # usage_events.source of this chat's turns
    NAME = "Mirel"

    def __init__(self, ws, runner, claude=None):
        """runner: the task runner (its db and events); claude() → the Claude Code argv (tests pass a fake)."""
        self.ws = ws
        self.runner = runner
        self.claude = claude or claude_command
        self.busy = threading.Lock()
        self.proc = None
        db = runner._db()
        have = {r[1] for r in db.execute("PRAGMA table_info(dashboard_chat)")}
        if have and "channel" not in have:   # older workspaces: every message so far was Mirel's
            db.execute("ALTER TABLE dashboard_chat ADD COLUMN channel TEXT NOT NULL DEFAULT 'mirel'")
        db.executescript(SCHEMA)
        db.commit()
        db.close()

    def _system(self):
        return prompts.MIREL

    def _context(self, db):
        """→ (what the turn prompt wraps the user's text with, extra state for after the reply)."""
        questions = open_questions(self.ws, db)
        return (lambda text: prompts.mirel_turn(questions, text or prompts.MIREL_START)), questions

    def _after(self, questions):
        """Mirel: re-match the waiting forms and unblock the jobs that only waited on questions."""
        stems = sorted({a["application"] for q in questions for a in q["applications"]})
        db = self.runner._db()
        try:
            moved = rematch(self.ws, db, stems)
        finally:
            db.close()
        for job_id, _, _ in moved:
            self._message("system", f"Jobul {job_id} are acum toate răspunsurile: intră la următoarea rulare "
                                    "„Aplică automat”.")

    def _questions(self, db):
        return open_questions(self.ws, db)

    def _emit(self, event):
        self.runner._emit({**event, "channel": self.CHANNEL})

    # --- state --------------------------------------------------------------------------------------------------
    def state(self):
        db = self.runner._db()
        try:
            messages = [dict(r) for r in db.execute(
                "SELECT id, role, text, at FROM dashboard_chat WHERE channel = ? ORDER BY id", (self.CHANNEL,))]
            questions = self._questions(db)
        finally:
            db.close()
        return {"messages": messages, "questions": questions, "busy": self.busy.locked()}

    def _kv(self, db, key, value=None, write=False):
        if write:
            db.execute("INSERT OR REPLACE INTO dashboard_kv (key, value) VALUES (?, ?)", (key, value))
            db.commit()
            return value
        row = db.execute("SELECT value FROM dashboard_kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _message(self, role, text):
        db = self.runner._db()
        try:
            cur = db.execute("INSERT INTO dashboard_chat (role, text, at, channel) VALUES (?, ?, ?, ?)",
                             (role, text, now_utc(), self.CHANNEL))
            db.commit()
            msg = {"id": cur.lastrowid, "role": role, "text": text, "at": now_utc()}
        finally:
            db.close()
        self._emit({"type": "chat", "message": msg})
        return msg

    def reset(self):
        if self.busy.locked():
            raise RuntimeError(f"{self.NAME} is answering; wait for the reply")
        db = self.runner._db()
        try:
            db.execute("DELETE FROM dashboard_chat WHERE channel = ?", (self.CHANNEL,))
            self._kv(db, self.SESSION_KEY, None, write=True)
        finally:
            db.close()
        self._emit({"type": "chat_reset"})

    # --- a turn -------------------------------------------------------------------------------------------------
    def send(self, text=None):
        """Starts a turn (text None = Mirel opens the conversation). RuntimeError while a turn runs."""
        text = (text or "").strip()
        if not self.busy.acquire(blocking=False):
            raise RuntimeError(f"{self.NAME} is answering; wait for the reply")
        try:
            if text:
                self._message("user", text)
            threading.Thread(target=self._turn, args=(text,), daemon=True, name=self.CHANNEL).start()
        except Exception:
            self.busy.release()
            raise

    def _turn(self, text):
        try:
            db = self.runner._db()
            try:
                wrap, extra = self._context(db)
                session = self._kv(db, self.SESSION_KEY)
            finally:
                db.close()
            self._emit({"type": "chat_busy", "busy": True})
            prompt = wrap(text)
            try:
                reply, session = self._ask(prompt, session)
            except RuntimeError as e:   # a session the CLI no longer has → start a new one
                if not session or not re.search(r"session|conversation", str(e), re.I):
                    raise
                reply, session = self._ask(prompt, None)
            if session:
                db = self.runner._db()
                self._kv(db, self.SESSION_KEY, session, write=True)
                db.close()
            self._message("mirel", reply)   # role "mirel" = the assistant's side in every channel
            self._after(extra)
        except Exception as e:  # the chat must never stay stuck on "busy"
            self._message("system", f"Eroare: {e}")
        finally:
            self.busy.release()
            self._emit({"type": "chat_busy", "busy": False})

    def _ask(self, prompt, session):
        """One Claude Code turn → (reply text, session id). Tool use shows as short status lines in the chat."""
        argv = self.claude() + ["--append-system-prompt", self._system()] + (["--resume", session] if session else [])
        env = None
        key = self.ws.env("ANTHROPIC_API_KEY")
        if key:
            env = {**os.environ, "ANTHROPIC_API_KEY": key}
        self.proc = subprocess.Popen(argv, cwd=self.ws.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        self.proc.stdin.write(prompt)
        self.proc.stdin.close()
        result, error, other, usage = None, False, [], None
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                if line.strip():
                    other.append(line.strip())
                continue
            session = event.get("session_id") or session
            if event.get("type") == "assistant":
                for part in (event.get("message") or {}).get("content") or []:
                    if part.get("type") == "tool_use":
                        self._emit({"type": "chat_tool", "name": part.get("name")})
            elif event.get("type") == "result":
                result, error, usage = event.get("result") or "", bool(event.get("is_error")), claude_usage(event)
        self.proc.wait()
        self.proc = None
        if usage:   # the chat's tokens count in the Costuri tab like every task's
            db = self.runner._db()
            try:
                record_usage(db, self.SOURCE, usage, kind=self.SOURCE, label=self.SOURCE)
            except Exception as e:  # never lose the reply over the usage row
                print(f"chat usage not saved: {e}")
            finally:
                db.close()
        if result is None or error:
            detail = result or " ".join(other)[-300:] or "no reply"
            if re.search(r"authenticat|not logged in|OAuth", detail, re.I):
                detail = "Claude CLI nu este autentificat: în terminal rulează `claude`, apoi /login."
            raise RuntimeError(detail)
        return result, session


class OnboardingGuide(Mirel):
    """The onboarding's guided questions (tab "Început", step 5): metrics, production status and interview checks
    for the confirmed facts; Claude writes the answers into onboarding/enrichment.md."""
    CHANNEL = "onboarding"
    SESSION_KEY = "onboarding_session"
    SOURCE = "onboarding"
    NAME = "The guide"

    def _system(self):
        return prompts.GUIDE

    def _context(self, db):
        from . import onboarding
        facts = onboarding.facts_summary(self.ws)
        return (lambda text: prompts.guide_turn(facts, text or prompts.GUIDE_START)), None

    def _after(self, extra):
        pass

    def _questions(self, db):
        return []
