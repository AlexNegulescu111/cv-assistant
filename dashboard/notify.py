"""Notifications: what the user should know or do, kept in the workspace db and shown under the dashboard's bell,
optionally also as a Windows toast (clicking it opens the dashboard at the job).

Raised when a task ends: an auto-apply summary (submitted / waiting for you / errors), a CV ready, a failed task,
and one notification per job that entered needs_human since the previous check.
"""
import os
import re
import subprocess
import sys
from xml.sax.saxutils import escape

from jobpilot.store import now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,   -- needs_input | run | cv | error
    title TEXT NOT NULL, body TEXT, job_id INTEGER, task_id INTEGER, read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dashboard_kv (key TEXT PRIMARY KEY, value TEXT);
"""
RESULT = re.compile(r"^RESULT (\S+)\s+(\d+)", re.M)
LEFT = re.compile(r"^(?:LEFT (\d+)|NO_CANDIDATES)", re.M)   # `apply run`'s closing line; NO_CANDIDATES → ""
POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
TOAST_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:JOBPILOT_TOAST)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:JOBPILOT_APP).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def toast(title, body, url):
    """A Windows toast; clicking it opens url. Texts go in through the environment, escaped, never into the script."""
    if sys.platform != "win32":
        return False
    xml = (f'<toast activationType="protocol" launch="{escape(url, {chr(34): "&quot;"})}">'
           f'<visual><binding template="ToastGeneric"><text>{escape(title)}</text><text>{escape(body or "")}</text>'
           f"</binding></visual></toast>")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST_SCRIPT],
                           env={**os.environ, "JOBPILOT_TOAST": xml, "JOBPILOT_APP": POWERSHELL_APP_ID},
                           capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def auto_apply_summary(log):
    """The last RESULT per job in an auto-apply log → {kind: count}."""
    last = {}
    for kind, job_id in RESULT.findall(log or ""):
        last[job_id] = kind
    counts = {}
    for kind in last.values():
        counts[kind] = counts.get(kind, 0) + 1
    return counts


class Notifier:
    def __init__(self, ws, runner, port=8765, toaster=None):
        """toaster(title, body, url): default the module's toast(), looked up at call time (tests replace it)."""
        self.ws = ws
        self.runner = runner
        self.port = port
        self.toaster = toaster
        db = runner._db()
        db.executescript(SCHEMA)
        db.close()
        self.seen_needs_human = self._needs_human()
        runner.on_finish.append(self.on_task)

    # --- settings -----------------------------------------------------------------------------------------------
    def toasts_enabled(self):
        db = self.runner._db()
        try:
            row = db.execute("SELECT value FROM dashboard_kv WHERE key = 'toasts'").fetchone()
        finally:
            db.close()
        return row is None or row["value"] == "1"

    def set_toasts(self, enabled):
        db = self.runner._db()
        try:
            db.execute("INSERT OR REPLACE INTO dashboard_kv (key, value) VALUES ('toasts', ?)", ("1" if enabled else "0",))
            db.commit()
        finally:
            db.close()

    # --- store --------------------------------------------------------------------------------------------------
    def add(self, kind, title, body=None, job_id=None, task_id=None):
        db = self.runner._db()
        try:
            cur = db.execute("INSERT INTO dashboard_notifications (at, kind, title, body, job_id, task_id) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (now_utc(), kind, title, body, job_id, task_id))
            db.commit()
            note = dict(db.execute("SELECT * FROM dashboard_notifications WHERE id = ?", (cur.lastrowid,)).fetchone())
        finally:
            db.close()
        self.runner._emit({"type": "notification", "notification": note})
        if self.toasts_enabled():
            anchor = f"#job={job_id}" if job_id else (f"#task={task_id}" if task_id else "")
            (self.toaster or toast)(title, body, f"http://127.0.0.1:{self.port}/{anchor}")
        return note

    def list(self, limit=50):
        db = self.runner._db()
        try:
            rows = [dict(r) for r in db.execute("SELECT * FROM dashboard_notifications ORDER BY id DESC LIMIT ?", (limit,))]
            unread = db.execute("SELECT COUNT(*) FROM dashboard_notifications WHERE read = 0").fetchone()[0]
        finally:
            db.close()
        return {"notifications": rows, "unread": unread, "toasts": self.toasts_enabled()}

    def mark_read(self, ids=None):
        db = self.runner._db()
        try:
            if ids:
                db.execute(f"UPDATE dashboard_notifications SET read = 1 WHERE id IN ({','.join('?' * len(ids))})", ids)
            else:
                db.execute("UPDATE dashboard_notifications SET read = 1")
            db.commit()
        finally:
            db.close()
        self.runner._emit({"type": "notification_read"})

    # --- triggers -----------------------------------------------------------------------------------------------
    def _needs_human(self):
        db = self.runner._db()
        try:
            return {r["id"]: r for r in db.execute(
                "SELECT id, company, title, reason FROM jobs WHERE status = 'needs_human' AND closed_at IS NULL")}
        finally:
            db.close()

    def on_task(self, task):
        full = self.runner.get(task["id"], log=True)
        if task["kind"] == "auto_apply" and task["status"] == "done":
            c = auto_apply_summary(full["log"])
            parts = [f"{c[k]} {label}" for k, label in (("submitted", "trimise"), ("needs_human", "așteaptă input"),
                                                         ("write", "fără CV/texte"), ("error", "erori")) if c.get(k)]
            left = LEFT.findall(full["log"] or "")
            if left:   # the last run's line: how many verdict-apply jobs the next runs can still take
                parts.append(f"mai sunt {left[-1]} joburi peste prag" if left[-1] not in ("", "0") else
                             "nu mai sunt joburi peste prag (următoarea rulare colectează altele)")
            self.add("run", "Aplicarea automată s-a terminat", ", ".join(parts) or "Niciun job de aplicat azi.",
                     task_id=task["id"])
        elif task["kind"] == "apply_link" and task["status"] == "done":
            c = auto_apply_summary(full["log"])
            done = ("trimisă" if c.get("submitted") else "deja aplicat" if c.get("already_applied") or "RESULT already_applied" in full["log"] else
                    "așteaptă input" if c.get("needs_human") or c.get("write") else "simulare gata" if c.get("ready")
                    else "eroare" if c.get("error") else "nimic de făcut")
            self.add("run", f"Aplicare la link: {done}", task["title"].replace("Aplică la link — ", ""),
                     task_id=task["id"])
        elif task["kind"] == "cv" and task["status"] == "done":
            self.add("cv", "CV gata", task["title"].replace("CV personalizat — ", ""), task_id=task["id"])
        elif task["status"] == "failed":
            self.add("error", f"Eșuat: {task['title']}", (full["log"] or "").strip().splitlines()[-1][:200]
                     if full["log"] else None, task_id=task["id"])
        now = self._needs_human()
        for job_id, job in now.items():
            if job_id not in self.seen_needs_human:
                self.add("needs_input", f"Așteaptă input: {job['company'] or ''} – {job['title']}".strip(" –"),
                         (job["reason"] or "").split(" → ")[0][:200], job_id=job_id)
        self.seen_needs_human = now
