"""Job pipeline: one status per job, allowed transitions, an event log, and a sync with the application folders.

    collector:  new · filtered_out · duplicate          (re-evaluated by every `collect run`)
    pipeline:   new → cv_ready → form_ready → applied
                any open step → needs_human (login, CAPTCHA, a question only the user can answer) → back or applied
                any open step → skipped (the user said no) → new (reopened)
`applied` is final. Every change is written to the `events` table (who/when/why), so a job's history is auditable.

Usage:
    python -m jobpilot status [--workspace DIR] [--json]
        → syncs with the application folders first, then: counts per status, what needs the user, what is next.
    python -m jobpilot mark <id|uid> [...] --status <status> [--reason TEXT] [--force]
    python -m jobpilot link <id|uid> <application folder name>
        → ties a job to output/applications/<name>/ (done by `apply` steps; by hand for older applications)
    python -m jobpilot history <id|uid>

Sync (also run by `status`): a job with an application folder moves forward from what the folder shows -
form.json → form_ready; fill_report.txt with STATUS needs_human / validation_error → needs_human; its URL or folder
in the applied log (submitted / already_applied) → applied. Sync never moves a job backwards.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

from . import store
from .collect import canonical_url, find_rows, load_applied_urls
from .store import now_utc
from .workspace import Workspace, add_argument as add_workspace_argument

COLLECTOR = ("new", "filtered_out", "duplicate")
STEPS = ("new", "cv_ready", "form_ready", "needs_human", "applied")
STATUSES = COLLECTOR + ("cv_ready", "form_ready", "needs_human", "skipped", "applied")
OPEN = ("new", "cv_ready", "form_ready", "needs_human")
ALLOWED = {
    "new": {"cv_ready", "form_ready", "needs_human", "skipped", "applied"},
    "filtered_out": {"new", "skipped", "applied"},
    "duplicate": {"new", "skipped", "applied"},
    "cv_ready": {"form_ready", "needs_human", "skipped", "applied", "new"},
    "form_ready": {"needs_human", "skipped", "applied", "cv_ready"},
    "needs_human": {"form_ready", "cv_ready", "skipped", "applied"},
    "skipped": {"new"},
    "applied": set(),
}
RANK = {s: i for i, s in enumerate(("new", "cv_ready", "form_ready", "needs_human", "applied"))}


class TransitionError(ValueError):
    pass


def transition(db, job_id, to, note=None, force=False):
    """Moves one job to `to` and logs the event. → (from, to); same status → no event."""
    if to not in STATUSES:
        raise TransitionError(f"unknown status '{to}' (known: {', '.join(STATUSES)})")
    row = db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise TransitionError(f"no job {job_id}")
    frm = row["status"]
    if frm == to:
        return frm, to
    if not force and to not in ALLOWED.get(frm, set()):
        raise TransitionError(f"job {job_id}: {frm} → {to} is not allowed (allowed: {sorted(ALLOWED.get(frm, ())) or 'none'};"
                              " --force to override)")
    db.execute("UPDATE jobs SET status = ?, reason = ? WHERE id = ?", (to, note, job_id))
    db.execute("INSERT INTO events (job_id, at, from_status, to_status, note) VALUES (?, ?, ?, ?, ?)",
               (job_id, now_utc(), frm, to, ("forced: " if force and to not in ALLOWED.get(frm, set()) else "") + (note or "")))
    return frm, to


def link(db, job_id, application):
    db.execute("UPDATE jobs SET application = ? WHERE id = ?", (application, job_id))


def applied_rows(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("status") or "") in ("submitted", "already_applied")]


def _mtime(path):
    return dt.datetime.fromtimestamp(os.path.getmtime(path), tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def folder_state(folder, since=None):
    """What an application folder shows: form_ready / needs_human, or None. With `since` (the job's last event),
    only files written after it count, so a state the user already moved past is not applied again."""
    report = os.path.join(folder, "fill_report.txt")
    if os.path.exists(report) and (not since or _mtime(report) > since):
        with open(report, encoding="utf-8") as f:
            m = re.search(r"^STATUS (\S+)", f.read(), re.M)
        if m and m.group(1) in ("needs_human", "validation_error"):
            return "needs_human"
    form = os.path.join(folder, "form.json")
    if os.path.exists(form) and (not since or _mtime(form) > since):
        return "form_ready"
    return None


def sync(db, ws):
    """Moves linked jobs forward from their application folders and the applied log. → list of (id, from, to)."""
    changes = []
    rows_log = applied_rows(ws.path("applied_log"))
    by_folder = {r.get("application") for r in rows_log}
    urls = load_applied_urls(ws.path("applied_log"))
    for job in db.execute("SELECT * FROM jobs WHERE status IN (%s)" % ",".join("?" * len(OPEN)), OPEN).fetchall():
        target = None
        app = job["application"]
        if (app and app in by_folder) or canonical_url(job["url"]) in urls or canonical_url(job["apply_url"]) in urls:
            target = "applied"
        elif app:
            last = db.execute("SELECT MAX(at) FROM events WHERE job_id = ?", (job["id"],)).fetchone()[0]
            target = folder_state(os.path.join(ws.path("applications_dir"), app), last)
        if target and RANK.get(target, -1) > RANK.get(job["status"], -1) and target in ALLOWED[job["status"]]:
            transition(db, job["id"], target, "sync: " + ("applied log" if target == "applied" else f"folder {app}"))
            changes.append((job["id"], job["status"], target))
    db.commit()
    return changes


def summary(db):
    counts = {s: 0 for s in STATUSES}
    for r in db.execute("SELECT status, COUNT(*) n FROM jobs WHERE closed_at IS NULL OR status = 'applied' GROUP BY status"):
        counts[r["status"]] = r["n"]
    latest = "(SELECT verdict FROM scores WHERE job_id = j.id ORDER BY id DESC LIMIT 1)"
    queues = {
        "needs_you": db.execute("SELECT j.id, j.company, j.title, j.application, j.reason FROM jobs j "
                                "WHERE j.status = 'needs_human' AND j.closed_at IS NULL ORDER BY j.id").fetchall(),
        "unscored": db.execute(f"SELECT COUNT(*) FROM jobs j WHERE j.status = 'new' AND j.closed_at IS NULL "
                               f"AND {latest} IS NULL").fetchone()[0],
        "to_apply": db.execute(f"SELECT j.id, j.company, j.title, j.status, {latest} verdict FROM jobs j "
                               f"WHERE j.status IN ('new', 'cv_ready', 'form_ready') AND j.closed_at IS NULL "
                               f"AND {latest} = 'apply' ORDER BY j.id").fetchall(),
    }
    return counts, queues


def main(argv=None, ws=None):
    ap = argparse.ArgumentParser(prog="jobpilot", description=__doc__.split("\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    add_workspace_argument(common)
    common.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", parents=[common])
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("mark", parents=[common])
    p.add_argument("refs", nargs="+")
    p.add_argument("--status", required=True, choices=STATUSES)
    p.add_argument("--reason")
    p.add_argument("--force", action="store_true", help="allow a transition outside the normal flow")
    p = sub.add_parser("link", parents=[common])
    p.add_argument("ref")
    p.add_argument("application")
    p = sub.add_parser("history", parents=[common])
    p.add_argument("ref")
    a = ap.parse_args(argv)
    ws = ws or Workspace(a.workspace)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db = store.connect(a.db or ws.path("db"))

    if a.cmd == "mark":
        for r in find_rows(db, a.refs):
            try:
                frm, to = transition(db, r["id"], a.status, a.reason, a.force)
            except TransitionError as e:
                db.rollback()
                sys.exit(f"ERROR {e}")
            print(f"{r['id']} {r['title']}: {frm} → {to}")
        db.commit()
    elif a.cmd == "link":
        r = find_rows(db, [a.ref])[0]
        if not os.path.isdir(os.path.join(ws.path("applications_dir"), a.application)):
            print(f"NOTE no folder {a.application} under {ws.path('applications_dir')} yet")
        link(db, r["id"], a.application)
        db.commit()
        print(f"{r['id']} {r['title']} ↔ {a.application}")
        for jid, frm, to in sync(db, ws):
            print(f"SYNC {jid}: {frm} → {to}")
    elif a.cmd == "history":
        r = find_rows(db, [a.ref])[0]
        print(f"{r['id']} {r['title']} — {r['company']}  [{r['status']}]  application: {r['application'] or '-'}")
        for e in db.execute("SELECT * FROM events WHERE job_id = ? ORDER BY id", (r["id"],)):
            print(f"  {e['at']}  {e['from_status'] or '-':<12} → {e['to_status']:<12} {e['note'] or ''}")
    elif a.cmd == "status":
        changes = sync(db, ws)
        counts, q = summary(db)
        if a.json:
            print(json.dumps({"synced": changes, "counts": counts, "unscored": q["unscored"],
                              "needs_you": [dict(r) for r in q["needs_you"]],
                              "to_apply": [dict(r) for r in q["to_apply"]]}, ensure_ascii=False, indent=2))
            return 0
        for jid, frm, to in changes:
            print(f"SYNC {jid}: {frm} → {to}")
        print("  ".join(f"{s} {counts[s]}" for s in STATUSES))
        print(f"\nNEEDS YOU ({len(q['needs_you'])})")
        for r in q["needs_you"]:
            print(f"  {r['id']:>5}  {r['company'] or '':<16}  {r['title'][:50]:<50}  {r['application'] or ''}  {r['reason'] or ''}")
        print(f"\nREADY TO APPLY — verdict apply ({len(q['to_apply'])})")
        for r in q["to_apply"]:
            print(f"  {r['id']:>5}  {r['company'] or '':<16}  {r['title'][:50]:<50}  [{r['status']}]")
        if q["unscored"]:
            print(f"\nUNSCORED new jobs: {q['unscored']} (python -m jobpilot score score)")
    return 0
