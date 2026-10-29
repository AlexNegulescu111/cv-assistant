"""Automatic applications: the best-scored jobs, a daily cap, and a needs_human queue for what only the user can do.

Usage (every command takes --workspace, --db and --config, default the workspace apply.json):
    python -m jobpilot apply plan [--cap N] [--ids 12 34]
        → which jobs the next run takes: open jobs (new / cv_ready / form_ready) whose latest score verdict is `apply`,
          best match first, at most `top_n`, and never more than what is left of `daily_cap` today
          (submissions today = applied-log rows dated today with status submitted; daily_cap null = no cap). --ids picks jobs by hand
          (any verdict), still under the cap.
    python -m jobpilot apply run [--cap N] [--ids ...] [--dry-run] [--after-write]
        → for each planned job: application folder + link, extract the form (once), answer it from the answers pool
          (answers written earlier are kept), pick the CV, then fill and submit when nothing is missing.
          --dry-run stops before the browser fill (extract and matching still run). One RESULT line per job, then
          `LEFT <n>` (open verdict-apply jobs still to take) or `NO_CANDIDATES` when none is left.
          --after-write: the run right after Claude's write step - a job that still needs writing goes to
          needs_human, so the next run takes the next jobs instead of the same one again.
    python -m jobpilot apply set-cv <id> <pdf>      → the CV for this job (written by cv-for-job); new → cv_ready
    python -m jobpilot apply add <url> --cv <pdf> [--title T] [--company C] [--description-file F]
        → a job the user found (CV made from a pasted description) as a cv_ready job: `ADDED <id>`, then
          `apply run --ids <id>` applies to it like to a collected one. A link already collected is reused; a link in
          the applied log prints `RESULT already_applied` and adds nothing.
    python -m jobpilot apply resume <id>            → a needs_human job in a visible browser window: the script fills,
          the user solves the CAPTCHA / signs in / checks, the script (or the user) submits; the result is logged.

RESULT kinds:
    submitted / already_applied      → status applied (the page also logged it in the applied log)
    write                            → Claude's part: a tailored CV (cv-for-job, then set-cv) and/or free-text answers
                                       (needs_writing fields, written into the folder's answers.json); then run again
    needs_human                      → the user's part: CAPTCHA, login/account, a question the pool cannot answer
                                       (`add-answer` makes it reusable), a page the script could not finish; `resume`
    ready (dry run) / error

Never: solving CAPTCHAs, creating accounts, typing passwords, answering do_not_answer questions, submitting when
the answers pool says settings.auto_submit = false or apply.json says "submit": false (those runs stop at needs_human
with "review and submit").
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys

from . import answers as answers_mod
from . import pipeline, store
from .collect import canonical_url, find_rows, fingerprint, load_applied_urls, slug
from .store import now_utc
from .workspace import Workspace, add_argument as add_workspace_argument

DEFAULTS = {
    "daily_cap": None,       # submissions per calendar day, counted from the applied log (any source); None = no cap
    "top_n": 3,              # jobs per run
    "min_match": 0,          # extra floor on the score match (0-100) on top of verdict `apply`
    "submit": True,          # also requires settings.auto_submit = true in the answers pool
    "headless": True,        # unattended fills run without a window; CAPTCHA / login pages go to needs_human
    "cv_by_family": {},      # {"Data": "output/pdf/CV_Data_EN.pdf"} - a ready CV per role family; else per-job CV
    "default_cv": None,      # used when nothing more specific exists; None = every job needs its own CV
    "wait_minutes": 60,      # `resume`: how long the window waits for the user
}
CANDIDATE_STATUSES = ("new", "cv_ready", "form_ready")
HUMAN_REASONS = ("unknown", "ask", "no_option", "do_not_answer")
LOGIN_NOTE = re.compile(r"login/account required|one-time code|sign ?in", re.I)
CAPTCHA_NOTE = re.compile(r"captcha", re.I)


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    return cfg


def pool_allows_submit(pool_path):
    if not os.path.exists(pool_path):
        return False
    with open(pool_path, encoding="utf-8") as f:
        return bool(json.load(f).get("settings", {}).get("auto_submit"))


def submitted_today(applied_log, today=None):
    today = (today or dt.date.today()).isoformat()
    if not os.path.exists(applied_log):
        return 0
    with open(applied_log, encoding="utf-8-sig", newline="") as f:
        return sum(1 for r in csv.DictReader(f) if r.get("date") == today and r.get("status") == "submitted")


LATEST = "(SELECT {col} FROM scores WHERE job_id = j.id ORDER BY id DESC LIMIT 1)"


def candidates(db, cfg, ids=None):
    q = (f"SELECT j.*, {LATEST.format(col='match')} AS match, {LATEST.format(col='verdict')} AS verdict, "
         f"{LATEST.format(col='role_family')} AS role_family FROM jobs j WHERE j.closed_at IS NULL "
         f"AND j.status IN ({','.join('?' * len(CANDIDATE_STATUSES))})")
    args = list(CANDIDATE_STATUSES)
    if ids:
        q += f" AND j.id IN ({','.join('?' * len(ids))})"
        args += list(ids)
    else:
        q += f" AND {LATEST.format(col='verdict')} = 'apply' AND COALESCE({LATEST.format(col='match')}, 0) >= ?"
        args.append(cfg["min_match"])
    return db.execute(q + " ORDER BY match DESC, j.id", args).fetchall()


def apply_threshold(ws):
    """scoring.json → thresholds.apply (the score from which a job's verdict is `apply`), None when not set."""
    path = ws.path("scoring")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return (json.load(f).get("thresholds") or {}).get("apply")


def print_left(db, ws, cfg):
    """How many open verdict-apply jobs later runs can still take (the ones taken now have left the list)."""
    left = len(candidates(db, cfg))
    floor = apply_threshold(ws)
    what = f"score >= {floor}" if floor is not None else "verdict apply"
    print(f"LEFT {left} open job(s) with {what}" if left else f"NO_CANDIDATES no open jobs left with {what}")


def plan(db, ws, cfg, ids=None, cap=None, today=None):
    """→ (jobs to take now, remaining cap today, done today)."""
    done = submitted_today(ws.path("applied_log"), today)
    limit = cfg["daily_cap"] if cap is None else cap
    remaining = None if limit is None else max(0, limit - done)  # None = no daily cap
    rows = candidates(db, cfg, ids)
    n = len(rows) if ids else cfg["top_n"]
    if remaining is not None:
        n = min(n, remaining)
    return rows[:n], remaining, done


def stem_for(job):
    s = f"{slug(job['company'] or 'company')}_{slug(job['title'])}".replace("-", "_")
    return s[:70].rstrip("_")


def pick_cv(ws, cfg, job):
    for path in (job["cv"], (cfg["cv_by_family"] or {}).get(job["role_family"] or ""), cfg["default_cv"]):
        if path and os.path.exists(ws.rel(path)):
            return ws.rel(path)
    return None


class PageRunner:
    """Runs jobpilot.page in a child process (a browser per job, output captured) → (status, reason, output)."""

    def __init__(self, ws):
        self.ws = ws
        self.base = [sys.executable, "-I", "-c",
                     "import sys; sys.path.insert(0, sys.argv.pop(1)); from jobpilot import page; sys.exit(page.main())",
                     os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]

    def _run(self, args):
        r = subprocess.run(self.base + args + ["--workspace", self.ws.root], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        m = re.findall(r"^STATUS (\S+)(?: - (.*))?$", out, re.M)
        if m:
            return m[-1][0], m[-1][1], out
        return ("ok" if r.returncode == 0 else "error"), (out.strip().splitlines() or [""])[-1][:200], out

    def extract(self, url, out):
        return self._run(["extract", url, "--out", out])

    def fill(self, form, answers, cv, submit, headless, wait_minutes=None, visible=False):
        args = ["fill", "--form", form, "--answers", answers]
        if cv:
            args += ["--cv", cv]
        if submit:
            args.append("--submit")
        if headless and not visible:
            args.append("--headless")
        if wait_minutes:
            args += ["--wait-minutes", str(wait_minutes)]
        return self._run(args)


def _set(db, job_id, to, note):
    """Moves forward / to needs_human when the flow allows it; otherwise only updates the reason."""
    try:
        pipeline.transition(db, job_id, to, note)
    except pipeline.TransitionError:
        db.execute("UPDATE jobs SET reason = ? WHERE id = ?", (note, job_id))


def prepare(db, ws, cfg, job, runner):
    """Folder, form, answers, CV → dict(stem, folder, form, answers_path, cv, write, human, blocked_page, error)."""
    stem = job["application"] or stem_for(job)
    folder = os.path.join(ws.path("applications_dir"), stem)
    if not job["application"]:
        pipeline.link(db, job["id"], stem)
    info = {"stem": stem, "folder": folder, "write": [], "human": [], "page": None, "error": None}
    form_path = os.path.join(folder, "form.json")
    if not os.path.exists(form_path):
        os.makedirs(folder, exist_ok=True)
        db.commit()  # never hold the db write lock while a browser runs (the dashboard writes its log meanwhile)
        status, reason, _ = runner.extract(job["apply_url"] or job["url"], folder)
        if not os.path.exists(form_path):
            info["error"] = f"extract failed: {reason}"
            return info
    with open(form_path, encoding="utf-8") as f:
        form = json.load(f)
    if job["status"] in ("new", "cv_ready"):
        _set(db, job["id"], "form_ready", "form extracted")
    notes = " | ".join(form.get("notes") or [])
    if LOGIN_NOTE.search(notes):
        info["page"] = "login or account required"
    elif CAPTCHA_NOTE.search(notes):
        info["page"] = "CAPTCHA on the page"
    pool = answers_mod.load_pool(ws.path("answers_pool"))
    answers, pending, _ = answers_mod.match_form(form["fields"], pool)
    answers_path = os.path.join(folder, "answers.json")
    answers, pending, _ = answers_mod.keep_written(answers, pending, answers_path)
    with open(answers_path, "w", encoding="utf-8") as f:
        json.dump({"answers": answers}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(folder, "pending.json"), "w", encoding="utf-8") as f:
        json.dump({"pending": pending}, f, ensure_ascii=False, indent=2)
    for p in pending:
        if not p["required"]:
            continue
        if p["reason"] == "needs_writing":
            info["write"].append(p["field_id"])
        elif p["reason"] in HUMAN_REASONS:
            info["human"].append(f"{p['reason']}: {p['label'][:60]}")
    if not form["fields"]:
        info["human"].append("no form fields read (multi-step or account first)")
    info["cv"] = pick_cv(ws, cfg, job)
    if not info["cv"]:
        info["write"].insert(0, "CV")
    info.update(form_path=form_path, answers_path=answers_path)
    return info


def process(db, ws, cfg, job, runner, submit, dry_run=False, after_write=False):
    """One job through the steps → (result kind, detail)."""
    info = prepare(db, ws, cfg, job, runner)
    if info["error"]:
        _set(db, job["id"], "needs_human", info["error"])
        return "error", info["error"]
    human = list(info["human"]) + ([info["page"]] if info["page"] else [])
    if info["write"]:
        if after_write and not dry_run:  # Claude already had its turn: out of the rotation, into the user's queue
            note = (f"Claude did not write: {', '.join(info['write'])}" + (f"; {'; '.join(human)}" if human else "")
                    + f" → write it, then python -m jobpilot apply run --ids {job['id']}")
            _set(db, job["id"], "needs_human", note)
            return "needs_human", note
        note = "write: " + ", ".join(info["write"]) + (f" (then the user: {'; '.join(human)})" if human else "")
        db.execute("UPDATE jobs SET reason = ? WHERE id = ?", (note, job["id"]))
        return "write", note
    if human:
        note = "; ".join(human) + f" → python -m jobpilot apply resume {job['id']}"
        _set(db, job["id"], "needs_human", note)
        return "needs_human", note
    if dry_run:
        return "ready", f"{info['stem']} (cv: {os.path.basename(info['cv'])}){'' if submit else ' - auto-submit off'}"
    if not submit:
        note = f"review and submit (auto-submit is off) → python -m jobpilot apply resume {job['id']}"
        _set(db, job["id"], "needs_human", note)
        return "needs_human", note
    db.commit()
    status, reason, _ = runner.fill(info["form_path"], info["answers_path"], info["cv"], True, cfg["headless"])
    if status in ("submitted", "already_applied", "skipped_already_applied"):
        _set(db, job["id"], "applied", f"{status} by apply run")
        return "already_applied" if status != "submitted" else "submitted", info["stem"]
    note = f"{status}: {reason or 'see fill_report.txt'} → python -m jobpilot apply resume {job['id']}"
    _set(db, job["id"], "needs_human", note)
    return "needs_human", note


def add_manual(db, ws, url, cv, title=None, company=None, description=None):
    """A job link the user brought → (job id, "added" | "exists" | "already_applied")."""
    key = canonical_url(url)
    if key in load_applied_urls(ws.path("applied_log")):
        return None, "already_applied"
    found = next((r for r in db.execute("SELECT * FROM jobs WHERE url IS NOT NULL OR apply_url IS NOT NULL")
                  if key in (canonical_url(r["url"]), canonical_url(r["apply_url"]))), None)
    if found:
        job_id, kind = found["id"], "exists"
        if description and not found["description"]:
            db.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))
    else:
        host = (re.match(r"[^/]+", key) or [""])[0]
        title = (title or "").strip() or "Job"
        now = now_utc()
        job_id = db.execute(
            "INSERT INTO jobs (uid, source, board, job_id, company, title, url, apply_url, description, fingerprint, "
            "first_seen, last_seen) VALUES (?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"manual:{host}:{key}", host, key, company, title, url, url, description,
             fingerprint(company, title, ""), now, now)).lastrowid
        kind = "added"
    row = db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    db.execute("UPDATE jobs SET cv = ? WHERE id = ?", (os.path.relpath(cv, ws.root), job_id))
    if row["status"] in ("new", "filtered_out", "duplicate", "skipped"):
        pipeline.transition(db, job_id, "cv_ready", "CV from a pasted description; link added by hand", force=True)
    return job_id, kind


def resume(db, ws, cfg, job, runner, submit):
    info = prepare(db, ws, cfg, job, runner)
    if info["error"] or info["write"]:
        return "write" if info["write"] else "error", info["error"] or "write: " + ", ".join(info["write"])
    db.commit()  # the window may stay open for up to wait_minutes
    status, reason, _ = runner.fill(info["form_path"], info["answers_path"], info["cv"], submit, False,
                                    cfg["wait_minutes"], visible=True)
    pipeline.sync(db, ws)  # the page logs a submit the user made in the window
    row = db.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    if row["status"] == "applied" or status in ("submitted", "already_applied"):
        _set(db, job["id"], "applied", f"{status} via resume")
        return "submitted", info["stem"]
    _set(db, job["id"], "needs_human", f"{status}: {reason or 'window closed without a confirmation'}")
    return "needs_human", reason


def main(argv=None, ws=None, runner=None, today=None):
    ap = argparse.ArgumentParser(prog="jobpilot apply", description=__doc__.split("\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    add_workspace_argument(common)
    common.add_argument("--db")
    common.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--ids", nargs="+", type=int)
        p.add_argument("--cap", type=int, help="today's cap instead of apply.json daily_cap")
        if name == "run":
            p.add_argument("--dry-run", action="store_true", help="extract + match only, no browser fill")
            p.add_argument("--after-write", action="store_true",
                           help="the run after Claude's write step: jobs still needing writing go to needs_human")
    p = sub.add_parser("set-cv", parents=[common])
    p.add_argument("ref")
    p.add_argument("pdf")
    p = sub.add_parser("resume", parents=[common])
    p.add_argument("ref")
    p = sub.add_parser("add", parents=[common])
    p.add_argument("url")
    p.add_argument("--cv", required=True)
    p.add_argument("--title")
    p.add_argument("--company")
    p.add_argument("--description-file")
    a = ap.parse_args(argv)
    ws = ws or Workspace(a.workspace)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config(a.config or ws.path("apply_config"))
    db = store.connect(a.db or ws.path("db"))
    runner = runner or PageRunner(ws)
    submit = bool(cfg["submit"]) and pool_allows_submit(ws.path("answers_pool"))

    if a.cmd == "set-cv":
        job = find_rows(db, [a.ref])[0]
        path = ws.rel(a.pdf)
        if not os.path.exists(path):
            sys.exit(f"ERROR no file {path}")
        db.execute("UPDATE jobs SET cv = ? WHERE id = ?", (os.path.relpath(path, ws.root), job["id"]))
        if job["status"] == "new":
            pipeline.transition(db, job["id"], "cv_ready", "CV set")
        db.commit()
        print(f"{job['id']} {job['title']}: cv = {os.path.relpath(path, ws.root)}")
        return 0

    if a.cmd == "add":
        path = ws.rel(a.cv)
        if not os.path.exists(path):
            sys.exit(f"ERROR no file {path}")
        description = None
        if a.description_file:
            with open(ws.rel(a.description_file), encoding="utf-8") as f:
                description = f.read().strip() or None
        job_id, kind = add_manual(db, ws, a.url, path, a.title, a.company, description)
        db.commit()
        if kind == "already_applied":
            print(f"RESULT already_applied  {a.url}  (in the applied log)")
        else:
            print(f"{'ADDED' if kind == 'added' else 'EXISTS'} {job_id}  {a.url}  cv = {os.path.relpath(path, ws.root)}")
        return 0

    if a.cmd == "resume":
        job = find_rows(db, [a.ref])[0]
        job = candidates(db, cfg, [job["id"]]) or [dict(job, match=None, verdict=None, role_family=None)]
        kind, detail = resume(db, ws, cfg, job[0], runner, submit)
        db.commit()
        print(f"RESULT {kind:<12} {job[0]['id']:>5}  {job[0]['title'][:50]}  {detail or ''}")
        return 0

    pipeline.sync(db, ws)
    db.commit()
    jobs, remaining, done = plan(db, ws, cfg, a.ids, a.cap, today)
    left = "no daily cap" if remaining is None else \
        f"{remaining} left (daily_cap {cfg['daily_cap'] if a.cap is None else a.cap})"
    print(f"CAP {done} submitted today, {left}, top_n {cfg['top_n']}; auto-submit {'on' if submit else 'off'}")
    if a.cmd == "plan":
        for j in jobs:
            print(f"  {j['id']:>5}  {j['match'] if j['match'] is not None else '-':>5}  {j['verdict'] or '-':<8}  "
                  f"{(j['company'] or '')[:16]:<16}  {j['title'][:50]:<50}  [{j['status']}]")
        print(f"{len(jobs)} job(s) planned")
        print_left(db, ws, cfg)
        return 0
    counts = {}
    for j in jobs:
        try:
            kind, detail = process(db, ws, cfg, j, runner, submit, a.dry_run, a.after_write)
        except Exception as e:  # one broken page never stops the run
            kind, detail = "error", str(e)[:200]
            _set(db, j["id"], "needs_human", f"error: {detail}")
        db.commit()
        counts[kind] = counts.get(kind, 0) + 1
        print(f"RESULT {kind:<12} {j['id']:>5}  {(j['company'] or '')[:16]:<16}  {j['title'][:44]:<44}  {detail}")
    print("SUMMARY " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing to do"))
    print_left(db, ws, cfg)
    return 0
