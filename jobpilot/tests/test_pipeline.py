"""Offline tests for jobpilot.pipeline and jobpilot.workspace: transitions, events, sync, workspace paths.

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import csv
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import pipeline, store  # noqa: E402
from jobpilot.workspace import Workspace  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def add_job(db, i, status="new", url=None):
    db.execute("INSERT INTO jobs (uid, source, board, job_id, company, title, url, fingerprint, status, first_seen, last_seen) "
               "VALUES (?, 'ashby', 'x', ?, 'Acme', ?, ?, ?, ?, 'now', 'now')",
               (f"ashby:x:{i}", str(i), f"Job {i}", url or f"https://jobs.example.com/{i}", f"fp{i}", status))


def status(db, i):
    return db.execute("SELECT status FROM jobs WHERE id = ?", (i,)).fetchone()[0]


def make_ws(overrides=None):
    root = tempfile.mkdtemp()
    if overrides:
        with open(os.path.join(root, "workspace.json"), "w", encoding="utf-8") as f:
            json.dump(overrides, f)
    return Workspace(root)


def test_workspace():
    ws = make_ws({"answers_pool": "pools/answers.json"})
    check("override", ws.path("answers_pool"), os.path.join(ws.root, "pools", "answers.json"))
    check("default", ws.path("db"), os.path.join(ws.root, "output", "jobs.sqlite"))
    with open(ws.path("env_file"), "w", encoding="utf-8") as f:
        f.write("OTHER=1\nJOBPILOT_TEST_KEY='abc'\n")
    check("env from .env", ws.env("JOBPILOT_TEST_KEY"), "abc")
    try:
        make_ws({"answer_pool": "x"})
        FAILS.append("unknown workspace key accepted")
    except ValueError:
        pass


def test_transitions():
    db = store.connect(":memory:")
    for i in (1, 2, 3):
        add_job(db, i)
    check("forward", pipeline.transition(db, 1, "cv_ready", "cv built"), ("new", "cv_ready"))
    check("same status no event", pipeline.transition(db, 1, "cv_ready"), ("cv_ready", "cv_ready"))
    pipeline.transition(db, 1, "applied")
    try:
        pipeline.transition(db, 1, "new")
        FAILS.append("applied → new allowed")
    except pipeline.TransitionError:
        pass
    check("forced", pipeline.transition(db, 1, "new", "mistake", force=True), ("applied", "new"))
    events = [(e["from_status"], e["to_status"], e["note"]) for e in db.execute("SELECT * FROM events WHERE job_id = 1 ORDER BY id")]
    check("events", events, [("new", "cv_ready", "cv built"), ("cv_ready", "applied", ""), ("applied", "new", "forced: mistake")])
    pipeline.transition(db, 2, "skipped")
    check("reopen skipped", pipeline.transition(db, 2, "new"), ("skipped", "new"))
    try:
        pipeline.transition(db, 3, "bogus")
        FAILS.append("unknown status accepted")
    except pipeline.TransitionError:
        pass


def test_sync():
    ws = make_ws()
    db = store.connect(ws.path("db"))
    for i in (1, 2, 3, 4, 5):
        add_job(db, i)
    apps = ws.path("applications_dir")
    for name, files in {"a_form": {"form.json": "{}"},
                        "b_human": {"form.json": "{}", "fill_report.txt": "form: x\nSTATUS needs_human - CAPTCHA\n"},
                        "c_sent": {"form.json": "{}"}}.items():
        os.makedirs(os.path.join(apps, name))
        for fn, text in files.items():
            with open(os.path.join(apps, name, fn), "w", encoding="utf-8") as f:
                f.write(text)
    with open(ws.path("applied_log"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "application", "status", "job_url", "form_url"])
        w.writerow(["2026-10-25", "c_sent", "submitted", "", ""])
        w.writerow(["2026-10-25", "other", "submitted", "https://jobs.example.com/4", ""])
    pipeline.link(db, 1, "a_form")
    pipeline.link(db, 2, "b_human")
    pipeline.link(db, 3, "c_sent")
    db.commit()
    changes = pipeline.sync(db, ws)
    check("sync changes", sorted(changes), [(1, "new", "form_ready"), (2, "new", "needs_human"), (3, "new", "applied"),
                                            (4, "new", "applied")])
    check("unlinked untouched", status(db, 5), "new")
    pipeline.transition(db, 2, "form_ready", "user solved it")  # never moved backwards by a later sync
    db.commit()
    pipeline.sync(db, ws)
    check("sync never moves back", status(db, 2), "form_ready")
    counts, q = pipeline.summary(db)
    check("counts", (counts["applied"], counts["form_ready"], counts["new"]), (2, 2, 1))
    check("unscored", q["unscored"], 1)


def test_migration():
    path = os.path.join(tempfile.mkdtemp(), "old.sqlite")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, source TEXT NOT NULL, "
                "board TEXT NOT NULL, job_id TEXT NOT NULL, company TEXT, title TEXT NOT NULL, location TEXT, remote INTEGER, "
                "workplace TEXT, department TEXT, employment_type TEXT, url TEXT, apply_url TEXT, published_at TEXT, "
                "description TEXT, fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', reason TEXT, "
                "duplicate_of INTEGER, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, closed_at TEXT)")
    old.execute("INSERT INTO jobs (uid, source, board, job_id, title, fingerprint, first_seen, last_seen) "
                "VALUES ('a:b:1', 'a', 'b', '1', 'T', 'fp', 'now', 'now')")
    old.commit()
    old.close()
    db = store.connect(path)
    cols = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
    check("migrated columns", {"last_run", "application"} <= cols, True)
    check("rows kept", db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
    check("events table", db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


TESTS = (test_workspace, test_transitions, test_sync, test_migration)

if __name__ == "__main__":
    for t in TESTS:
        t()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all pipeline tests passed")
