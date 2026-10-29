"""Read-only views of a jobpilot workspace for the dashboard: the three cards, job lists and details, skills to learn.

Every path comes from the Workspace; nothing here writes except pipeline.sync (the same sync `jobpilot status` runs).
"""
import csv
import datetime as dt
import json
import os
import shutil

from jobpilot import apply as apply_mod, pipeline, skill_clusters, store
from jobpilot.collect import canonical_url

from . import runner

LATEST = "(SELECT {col} FROM scores WHERE job_id = j.id ORDER BY id DESC LIMIT 1)"
COLUMNS = ("j.id, j.company, j.title, j.location, j.status, j.reason, j.url, j.application, j.cv, j.first_seen, "
           f"{LATEST.format(col='match')} AS \"match\", {LATEST.format(col='verdict')} AS verdict, "
           f"{LATEST.format(col='role_family')} AS role_family")   # MATCH is an SQLite operator: quote the alias
CATEGORIES = {   # card → WHERE clause; in_progress = what `apply plan` would pick from
    "applied": "j.status = 'applied'",
    "in_progress": f"j.status IN ('new', 'cv_ready', 'form_ready') AND j.closed_at IS NULL "
                   f"AND {LATEST.format(col='verdict')} = 'apply'",
    "needs_input": "j.status = 'needs_human' AND j.closed_at IS NULL",
}


def connect(ws):
    return store.connect(ws.path("db"))


def overview(ws):
    """Syncs the pipeline with the application folders, then → counts for the cards and per status."""
    db = connect(ws)
    try:
        synced = pipeline.sync(db, ws)
        counts, queues = pipeline.summary(db)
        cards = {name: db.execute(f"SELECT COUNT(*) FROM jobs j WHERE {where}").fetchone()[0]
                 for name, where in CATEGORIES.items()}
        cards["applied"] = len(pipeline.applied_rows(ws.path("applied_log")))   # also applications made outside the collector
        return {"cards": cards, "statuses": counts, "unscored": queues["unscored"], "synced": len(synced)}
    finally:
        db.close()


def jobs(ws, category):
    if category not in CATEGORIES:
        raise KeyError(category)
    db = connect(ws)
    try:
        if category == "applied":
            return applied(ws, db)
        return [dict(r) for r in db.execute(f"SELECT {COLUMNS} FROM jobs j WHERE {CATEGORIES[category]} "
                                            "ORDER BY \"match\" DESC, j.id")]
    finally:
        db.close()


def applied(ws, db):
    """The applied log (newest first), each row joined to its collected job when there is one."""
    by_app, by_url = {}, {}
    for r in db.execute(f"SELECT {COLUMNS}, j.apply_url FROM jobs j WHERE {CATEGORIES['applied']} OR j.application IS NOT NULL"):
        r = dict(r)
        if r["application"]:
            by_app[r["application"]] = r
        for u in (r["url"], r.pop("apply_url")):
            if u:
                by_url[canonical_url(u)] = r
    registry = _read(ws.rel(os.path.join("notes", "applications.json")))   # draft stem → company / role, if kept
    registry = json.loads(registry) if registry else {}
    out = []
    for row in reversed(pipeline.applied_rows(ws.path("applied_log"))):
        job = by_app.get(row.get("application")) or by_url.get(canonical_url(row.get("job_url") or "")) or {}
        known = registry.get(row.get("application")) or {}
        out.append({**job, "id": job.get("id"), "application": row.get("application"), "applied_at": row.get("date"),
                    "log_status": row.get("status"), "url": job.get("url") or row.get("job_url"),
                    "company": job.get("company") or known.get("company"),
                    "title": job.get("title") or known.get("role") or row.get("application"), "status": "applied"})
    return out


def _read(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def application_dir(ws, name):
    """The application folder for a job, or None (the name comes from the db, never from the request)."""
    if not name:
        return None
    base = os.path.abspath(ws.path("applications_dir"))
    folder = os.path.abspath(os.path.join(base, name))
    return folder if folder.startswith(base + os.sep) and os.path.isdir(folder) else None


def job_detail(ws, job_id):
    db = connect(ws)
    try:
        row = db.execute(f"SELECT {COLUMNS}, j.description, j.apply_url FROM jobs j WHERE j.id = ?", (job_id,)).fetchone()
        if not row:
            return None
        job = dict(row)
        score = db.execute("SELECT scored_at, match, verdict, knockouts, review_flags, role_family FROM scores "
                           "WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        job["score"] = dict(score) if score else None
        job["events"] = [dict(e) for e in db.execute("SELECT at, from_status, to_status, note FROM events "
                                                     "WHERE job_id = ? ORDER BY id", (job_id,))]
    finally:
        db.close()
    folder = application_dir(ws, job["application"])
    pending = _read(os.path.join(folder, "pending.json")) if folder else None
    job["pending"] = json.loads(pending).get("pending", []) if pending else []
    job["fill_report"] = _read(os.path.join(folder, "fill_report.txt")) if folder else None
    job["has_prefill"] = bool(folder and os.path.exists(os.path.join(folder, "prefill.png")))
    return job


def prefill_png(ws, job_id):
    db = connect(ws)
    try:
        row = db.execute("SELECT application FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        db.close()
    folder = application_dir(ws, row["application"]) if row else None
    path = folder and os.path.join(folder, "prefill.png")
    return path if path and os.path.exists(path) else None


def skills_to_learn(ws):
    """demanded_skills_to_learn.csv (UTF-8 with BOM), the names for the same skill grouped (skill_clusters: local
    rules + skill_clusters.json) → {skills: one row per group, most asked first (distinct ads), rows: CSV rows,
    ungrouped: names the Claude grouping has not seen yet}. The CSV itself is never changed."""
    path = ws.rel("demanded_skills_to_learn.csv")
    if not os.path.exists(path):
        return {"skills": [], "rows": 0, "ungrouped": 0}
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("skill") or "").strip()]
    clusters = skill_clusters.load(ws)
    return {"skills": skill_clusters.merged(rows, clusters), "rows": len(rows),
            "ungrouped": len(skill_clusters.ungrouped(rows, clusters))}


def apply_state(ws):
    """What a click on "Aplică automat" would do: auto-submit on/off, today's cap (None = none), jobs per run, and how
    many open verdict-apply jobs are still left to take (candidates_left; score >= apply_threshold)."""
    cfg = apply_mod.load_config(ws.path("apply_config"))
    done = apply_mod.submitted_today(ws.path("applied_log"))
    cap = cfg["daily_cap"]
    db = connect(ws)
    try:   # what later runs can still take: each run takes the next top_n of these
        left = len(apply_mod.candidates(db, cfg))
    finally:
        db.close()
    return {"submit": bool(cfg["submit"]) and apply_mod.pool_allows_submit(ws.path("answers_pool")),
            "daily_cap": cap, "submitted_today": done, "remaining": None if cap is None else max(0, cap - done),
            "top_n": cfg["top_n"], "has_default_cv": bool(cfg["default_cv"] or cfg["cv_by_family"]),
            "candidates_left": left, "apply_threshold": apply_mod.apply_threshold(ws)}


def cv_file(ws, path):
    """A workspace-relative path → the absolute file when it is a PDF/DOCX inside the pdf or docx folder, else None."""
    full = os.path.abspath(ws.rel(path))
    allowed = [os.path.abspath(ws.path("pdf_dir")), os.path.abspath(ws.rel(os.path.join("output", "docx")))]
    if (full.lower().endswith((".pdf", ".docx")) and any(full.startswith(d + os.sep) for d in allowed)
            and os.path.isfile(full)):
        return full
    return None


def credentials(ws):
    """Only whether each credential is set, never its value."""
    claude = runner.claude_status()
    return {
        "TYPESAFE_API_KEY": bool(ws.env("TYPESAFE_API_KEY")),
        "ANTHROPIC_API_KEY": bool(ws.env("ANTHROPIC_API_KEY")),
        "claude_cli": claude["installed"],
        "claude_logged_in": claude["logged_in"],
        "codex_cli": bool(shutil.which("codex")),
        "browser_profile": os.path.isdir(ws.path("browser_profile")),
        "env_overrides": sorted(n for n in ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY") if os.environ.get(n)),
    }


# ---------- token usage (Costuri tab) ----------

def _local_day(at):
    """A stored UTC timestamp ("2026-10-29T07:52:05Z") → the local calendar day "2026-10-29"."""
    try:
        return dt.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).astimezone().date()
    except (TypeError, ValueError):
        return None


def usage_group(kind, label):
    """A Claude run → the line it counts on in the per-kind table (auto-apply's write steps get their own)."""
    if (label or "").startswith("write job"):
        return "write"
    return kind or "other"


def usage(ws, days=30, today=None):
    """Tokens over the last `days` local days: Claude runs (usage_events) and Jev scorings (scores).
    → {periods: {today, week, month}, series: [{day, claude, jev}], by_kind: [...], top_tasks: [...], averages}.
    Claude tokens = input + output + cache reads + cache writes; cost_usd is the CLI's API-equivalent estimate."""
    today = today or dt.date.today()
    first = today - dt.timedelta(days=days - 1)
    since = (dt.datetime.combine(first, dt.time()).astimezone().astimezone(dt.timezone.utc)
             - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")   # a day of slack; the local day decides
    db = connect(ws)
    try:
        db.executescript(runner.USAGE_SCHEMA)
        claude = [dict(r) for r in db.execute(
            "SELECT u.*, t.title FROM usage_events u LEFT JOIN dashboard_tasks t ON t.id = u.task_id "
            "WHERE u.at >= ?", (since,))] if _has_table(db, "dashboard_tasks") else \
            [dict(r, title=None) for r in db.execute("SELECT * FROM usage_events WHERE at >= ?", (since,))]
        jev = [dict(r) for r in db.execute(
            "SELECT scored_at, COALESCE(input_tokens, 0) AS input_tokens, COALESCE(output_tokens, 0) AS output_tokens "
            "FROM scores WHERE scored_at >= ?", (since,))]
    finally:
        db.close()
    for r in claude:
        r["day"] = _local_day(r["at"])
        r["cache"] = (r["cache_read_tokens"] or 0) + (r["cache_write_tokens"] or 0)
        r["tokens"] = (r["input_tokens"] or 0) + (r["output_tokens"] or 0) + r["cache"]
    for r in jev:
        r["day"] = _local_day(r["scored_at"])
        r["tokens"] = r["input_tokens"] + r["output_tokens"]
    claude = [r for r in claude if r["day"] and r["day"] >= first]
    jev = [r for r in jev if r["day"] and r["day"] >= first]

    def total(c_rows, j_rows):
        return {"claude": {"tokens": sum(r["tokens"] for r in c_rows),
                           "input": sum(r["input_tokens"] or 0 for r in c_rows),
                           "output": sum(r["output_tokens"] or 0 for r in c_rows),
                           "cache": sum(r["cache"] for r in c_rows),
                           "cost_usd": round(sum(r["cost_usd"] or 0 for r in c_rows), 4), "runs": len(c_rows)},
                "jev": {"tokens": sum(r["tokens"] for r in j_rows), "jobs": len(j_rows)}}

    def since_day(rows, d):
        return [r for r in rows if r["day"] >= d]

    periods = {name: total(since_day(claude, d), since_day(jev, d))
               for name, d in (("today", today), ("week", today - dt.timedelta(days=6)), ("month", first))}
    series = []
    for i in range(days):
        d = first + dt.timedelta(days=i)
        series.append({"day": d.isoformat(), "claude": sum(r["tokens"] for r in claude if r["day"] == d),
                       "jev": sum(r["tokens"] for r in jev if r["day"] == d)})
    groups = {}
    for r in claude:
        g = groups.setdefault(usage_group(r["kind"], r["label"]), {"runs": 0, "tokens": 0, "cost_usd": 0.0})
        g["runs"] += 1
        g["tokens"] += r["tokens"]
        g["cost_usd"] += r["cost_usd"] or 0
    by_kind = sorted(({"kind": k, **g, "cost_usd": round(g["cost_usd"], 4), "avg_tokens": g["tokens"] // g["runs"]}
                      for k, g in groups.items()), key=lambda g: -g["tokens"])
    tasks = {}
    for r in claude:
        if r["task_id"] is None:
            continue
        t = tasks.setdefault(r["task_id"], {"task_id": r["task_id"], "title": r["title"], "kind": r["kind"],
                                            "day": r["day"].isoformat(), "tokens": 0, "cost_usd": 0.0})
        t["tokens"] += r["tokens"]
        t["cost_usd"] += r["cost_usd"] or 0
    top_tasks = sorted(tasks.values(), key=lambda t: -t["tokens"])[:10]
    for t in top_tasks:
        t["cost_usd"] = round(t["cost_usd"], 4)
    return {"days": days, "periods": periods, "series": series, "by_kind": by_kind, "top_tasks": top_tasks,
            "averages": {"jev_per_job": periods["month"]["jev"]["tokens"] // max(1, len(jev)) if jev else None}}


def _has_table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone())
