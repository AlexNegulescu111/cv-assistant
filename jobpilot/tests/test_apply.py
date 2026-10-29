"""Offline tests for jobpilot.apply with a fake page runner: plan, daily cap, write / needs_human / submitted paths.

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import csv
import datetime as dt
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import apply, store  # noqa: E402
from jobpilot.workspace import Workspace  # noqa: E402

FAILS = []
TODAY = dt.date(2026, 10, 25)
POOL = {
    "settings": {"auto_submit": True},
    "contact": {"email": "maria@example.com"},
    "screening": {}, "voluntary_disclosures": {}, "do_not_answer": [], "consents": [],
    "question_bank": [{"id": "email", "patterns": ["e-?mail"], "answer": "{contact.email}"}],
    "free_text": {"patterns": ["why .* interested"]},
}
EMAIL = {"field_id": "f1_email", "label": "Email", "type": "text", "required": True}
CV = {"field_id": "f2_cv", "label": "Resume", "type": "file", "required": True}
WHY = {"field_id": "f3_why", "label": "Why are you interested in us?", "type": "textarea", "required": True}
ODD = {"field_id": "f4_odd", "label": "Favourite colour of our logo?", "type": "text", "required": True}
FORMS = {  # url → (fields, notes)
    "https://x/1": ([EMAIL, CV], []),
    "https://x/2": ([EMAIL, CV], ["CAPTCHA present - the user solves it"]),
    "https://x/3": ([EMAIL, CV, WHY], []),
    "https://x/4": ([EMAIL, CV], []),
    "https://x/5": ([EMAIL, CV, ODD], []),
    "https://x/6": ([EMAIL, CV], []),
}


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


class FakeRunner:
    def __init__(self, ws, fill_status=None):
        self.ws, self.fills, self.fill_status = ws, [], fill_status or {}

    def extract(self, url, out):
        fields, notes = FORMS[url]
        with open(os.path.join(out, "form.json"), "w", encoding="utf-8") as f:
            json.dump({"job_url": url, "form_url": url, "ats": "Ashby", "notes": notes, "fields": fields}, f)
        return "ok", "", ""

    def fill(self, form, answers, cv, submit, headless, wait_minutes=None, visible=False):
        url = json.load(open(form, encoding="utf-8"))["job_url"]
        self.fills.append((url, bool(cv), submit, headless, visible))
        status = self.fill_status.get(url, "submitted" if submit else "prefilled")
        if status == "submitted":  # like page.py: a confirmed submit is logged in the applied log
            with open(self.ws.path("applied_log"), "a", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow([TODAY.isoformat(), os.path.basename(os.path.dirname(form)), "submitted", url, url])
        return status, "", ""


def setup(apply_cfg=None, pool=None, submitted_before=0):
    ws = Workspace(tempfile.mkdtemp())
    json.dump(pool or POOL, open(ws.path("answers_pool"), "w", encoding="utf-8"))
    json.dump({"daily_cap": 5, "top_n": 10, "default_cv": "cv.pdf", **(apply_cfg or {})},
              open(ws.path("apply_config"), "w", encoding="utf-8"))
    open(os.path.join(ws.root, "cv.pdf"), "wb").write(b"%PDF")
    os.makedirs(ws.path("applications_dir"))
    with open(ws.path("applied_log"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "application", "status", "job_url", "form_url"])
        for i in range(submitted_before):
            w.writerow([TODAY.isoformat(), f"old{i}", "submitted", f"https://old/{i}", ""])
        w.writerow([(TODAY - dt.timedelta(days=1)).isoformat(), "yesterday", "submitted", "https://old/y", ""])
    db = store.connect(ws.path("db"))
    for i, (verdict, match) in enumerate([("apply", 90), ("apply", 85), ("apply", 80), ("apply", 75), ("apply", 72),
                                          ("review", 60)], 1):
        db.execute("INSERT INTO jobs (uid, source, board, job_id, company, title, url, apply_url, fingerprint, status, "
                   "first_seen, last_seen) VALUES (?, 'ashby', 'x', ?, 'Acme', ?, ?, ?, ?, 'new', 'now', 'now')",
                   (f"ashby:x:{i}", str(i), f"Job {i}", f"https://x/{i}", f"https://x/{i}", f"fp{i}"))
        db.execute("INSERT INTO scores (job_id, scored_at, match, verdict, role_family, key) VALUES (?, 'now', ?, ?, 'Data', 'k')",
                   (i, match, verdict))
    db.commit()
    return ws, db


def run(ws, argv, runner):
    buf = io.StringIO()
    with redirect_stdout(buf):
        apply.main(argv, ws=ws, runner=runner, today=TODAY)
    return buf.getvalue()


def results(out):
    return {int(line.split()[2]): line.split()[1] for line in out.splitlines() if line.startswith("RESULT")}


def status(ws, i):
    db = store.connect(ws.path("db"))
    return db.execute("SELECT status FROM jobs WHERE id = ?", (i,)).fetchone()[0]


def test_plan_and_cap():
    ws, db = setup({"top_n": 3})
    jobs, remaining, done = apply.plan(db, ws, apply.load_config(ws.path("apply_config")), today=TODAY)
    check("top_n best first, review excluded", [j["id"] for j in jobs], [1, 2, 3])
    check("cap counts only today", (remaining, done), (5, 0))
    ws, db = setup(submitted_before=4)
    jobs, remaining, _ = apply.plan(db, ws, apply.load_config(ws.path("apply_config")), today=TODAY)
    check("cap leaves one", ([j["id"] for j in jobs], remaining), ([1], 1))
    jobs, _, _ = apply.plan(db, ws, apply.load_config(ws.path("apply_config")), ids=[6, 2], today=TODAY)
    check("--ids any verdict, still capped", [j["id"] for j in jobs], [2])
    out = run(ws, ["run", "--cap", "0"], FakeRunner(ws))
    check("cap 0 → nothing", "SUMMARY nothing to do" in out, True)
    ws, db = setup({"daily_cap": None, "top_n": 10}, submitted_before=4)
    jobs, remaining, _ = apply.plan(db, ws, apply.load_config(ws.path("apply_config")), today=TODAY)
    check("no daily cap → every apply job up to top_n", (len(jobs), remaining), (5, None))
    check("no cap printed", "no daily cap" in run(ws, ["plan"], FakeRunner(ws)), True)


def test_run_paths():
    ws, db = setup({"default_cv": None})
    runner = FakeRunner(ws)
    run(ws, ["set-cv", "1", "cv.pdf"], runner)
    run(ws, ["set-cv", "2", "cv.pdf"], runner)
    run(ws, ["set-cv", "3", "cv.pdf"], runner)
    run(ws, ["set-cv", "5", "cv.pdf"], runner)
    check("set-cv → cv_ready", status(ws, 1), "cv_ready")
    r = results(run(ws, ["run"], runner))
    check("results", r, {1: "submitted", 2: "needs_human", 3: "write", 4: "write", 5: "needs_human"})
    check("submitted → applied", status(ws, 1), "applied")
    check("captcha → needs_human", status(ws, 2), "needs_human")
    check("writing pending stays form_ready", (status(ws, 3), status(ws, 4)), ("form_ready", "form_ready"))
    check("unattended fills are headless", runner.fills, [("https://x/1", True, True, True, False)])
    reason = store.connect(ws.path("db")).execute("SELECT reason FROM jobs WHERE id = 4").fetchone()[0]
    check("missing CV is Claude's task", reason.startswith("write: CV"), True)
    check("one submitted today", apply.submitted_today(ws.path("applied_log"), TODAY), 1)

    # Claude writes the free text and the CV, then runs again: both go through
    folder = os.path.join(ws.path("applications_dir"), "acme_job_3")
    a = json.load(open(os.path.join(folder, "answers.json"), encoding="utf-8"))
    a["answers"]["f3_why"] = "Because of the product."
    json.dump(a, open(os.path.join(folder, "answers.json"), "w", encoding="utf-8"))
    run(ws, ["set-cv", "4", "cv.pdf"], runner)
    r = results(run(ws, ["run"], runner))
    check("second run", r, {3: "submitted", 4: "submitted"})
    check("needs_human jobs are not retried", 2 in r or 5 in r, False)

    # the user solves the CAPTCHA in a visible window
    r = results(run(ws, ["resume", "2"], runner))
    check("resume submits", (r, status(ws, 2)), ({2: "submitted"}, "applied"))
    check("resume is visible", runner.fills[-1][-1], True)


def test_auto_submit_off_and_failures():
    ws, db = setup(pool={**POOL, "settings": {"auto_submit": False}})
    runner = FakeRunner(ws)
    r = results(run(ws, ["run", "--ids", "1"], runner))
    check("auto_submit off → review", (r, status(ws, 1), runner.fills), ({1: "needs_human"}, "needs_human", []))
    ws, db = setup({"submit": False})
    check("apply.json submit false", results(run(ws, ["run", "--ids", "1"], FakeRunner(ws))), {1: "needs_human"})
    ws, db = setup()
    runner = FakeRunner(ws, {"https://x/1": "validation_error", "https://x/6": "skipped_already_applied"})
    r = results(run(ws, ["run", "--ids", "1", "6"], runner))
    check("page failure → needs_human; already in log → applied", (r, status(ws, 1), status(ws, 6)),
          ({1: "needs_human", 6: "already_applied"}, "needs_human", "applied"))
    ws, db = setup()
    r = results(run(ws, ["run", "--ids", "1", "--dry-run"], FakeRunner(ws)))
    check("dry run stops before the fill", (r, status(ws, 1)), ({1: "ready"}, "form_ready"))


def test_add_manual():
    ws, db = setup({"default_cv": None})
    FORMS["https://careers.other.com/jobs/42"] = ([EMAIL, CV, WHY], [])
    desc = os.path.join(ws.root, "pasted.md")
    open(desc, "w", encoding="utf-8").write("Senior Engineer\nWe need Python.")
    out = run(ws, ["add", "https://careers.other.com/jobs/42", "--cv", "cv.pdf", "--title", "Senior Engineer",
                   "--company", "Other", "--description-file", desc], FakeRunner(ws))
    check("added", out.startswith("ADDED 7 "), True)
    row = store.connect(ws.path("db")).execute("SELECT * FROM jobs WHERE id = 7").fetchone()
    check("manual job row", (row["source"], row["status"], row["cv"], row["description"]),
          ("manual", "cv_ready", "cv.pdf", "Senior Engineer\nWe need Python."))
    again = run(ws, ["add", "https://careers.other.com/jobs/42/", "--cv", "cv.pdf"], FakeRunner(ws))
    check("same link reused", again.startswith("EXISTS 7 "), True)
    check("collected link reused", run(ws, ["add", "https://x/2", "--cv", "cv.pdf"], FakeRunner(ws)).startswith("EXISTS 2 "),
          True)
    check("collected job gets the CV", status(ws, 2), "cv_ready")
    check("applied link refused", "RESULT already_applied" in run(ws, ["add", "https://old/y", "--cv", "cv.pdf"],
                                                                   FakeRunner(ws)), True)
    runner = FakeRunner(ws)
    check("then applies like a collected job", results(run(ws, ["run", "--ids", "7"], runner)), {7: "write"})
    folder = os.path.join(ws.path("applications_dir"), "other_senior_engineer")
    a = json.load(open(os.path.join(folder, "answers.json"), encoding="utf-8"))
    a["answers"]["f3_why"] = "Because."
    json.dump(a, open(os.path.join(folder, "answers.json"), "w", encoding="utf-8"))
    check("submitted after the write", results(run(ws, ["run", "--ids", "7"], runner)), {7: "submitted"})
    del FORMS["https://careers.other.com/jobs/42"]


def test_rotation():
    """Each run takes the next top_n verdict-apply jobs; a job Claude could not write for leaves the rotation."""
    ws, db = setup({"top_n": 2, "daily_cap": None})
    runner = FakeRunner(ws)
    out = run(ws, ["run"], runner)
    check("run 1 takes the best two", results(out), {1: "submitted", 2: "needs_human"})
    check("run 1 says what is left", "LEFT 3 open job(s)" in out, True)
    out = run(ws, ["run"], runner)
    check("run 2 takes the next two", results(out), {3: "write", 4: "submitted"})
    out = run(ws, ["run", "--ids", "3", "--after-write"], runner)   # Claude's write step left job 3 unwritten
    check("still unwritten after the write step → needs_human", (results(out), status(ws, 3)),
          ({3: "needs_human"}, "needs_human"))
    reason = store.connect(ws.path("db")).execute("SELECT reason FROM jobs WHERE id = 3").fetchone()[0]
    check("the reason says Claude did not write", reason.startswith("Claude did not write: f3_why"), True)
    out = run(ws, ["run"], runner)
    check("run 3 takes job 5, not job 3 again", results(out), {5: "needs_human"})
    check("nothing left", "NO_CANDIDATES" in out, True)
    check("plan says so too", "NO_CANDIDATES" in run(ws, ["plan"], runner), True)
    json.dump({"thresholds": {"apply": 40}}, open(ws.path("scoring"), "w", encoding="utf-8"))
    check("threshold from scoring.json", "with score >= 40" in run(ws, ["plan"], runner), True)
    ws, db = setup({"top_n": 10})
    out = run(ws, ["run", "--ids", "3", "--after-write", "--dry-run"], FakeRunner(ws))
    check("a dry run never moves an unwritten job", (results(out), status(ws, 3)), ({3: "write"}, "form_ready"))


TESTS = (test_plan_and_cap, test_run_paths, test_auto_submit_off_and_failures, test_add_manual,
         test_rotation)

if __name__ == "__main__":
    for t in TESTS:
        t()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all apply tests passed")
