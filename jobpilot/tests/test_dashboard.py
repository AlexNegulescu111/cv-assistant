"""Offline tests for the dashboard API (cv-assistant/dashboard): cards, job lists, details, skills, credentials.
Skipped when FastAPI is not installed.

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import csv
import json
import os
import sys
import tempfile
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient
except ImportError:
    print("SKIP fastapi not installed")
    sys.exit(0)
from dashboard.app import create_app  # noqa: E402
from dashboard import runner as runner_mod  # noqa: E402
from dashboard.runner import Runner  # noqa: E402

runner_mod.claude_status = lambda: {"installed": True, "logged_in": False}   # no real CLI call in tests
from dashboard import notify as notify_mod  # noqa: E402

TOASTS = []
notify_mod.toast = lambda title, body, url: TOASTS.append((title, body, url))   # no real Windows toasts in tests
from jobpilot import store  # noqa: E402
from jobpilot.workspace import Workspace  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def make_ws():
    ws = Workspace(tempfile.mkdtemp())
    db = store.connect(ws.path("db"))
    jobs = [  # id, status, verdict, application
        (1, "applied", "apply", "acme_data"),
        (2, "new", "apply", None),
        (3, "new", "low", None),
        (4, "needs_human", "apply", "beta_ml"),
    ]
    for i, status, verdict, app in jobs:
        db.execute("INSERT INTO jobs (uid, source, board, job_id, company, title, url, fingerprint, status, first_seen, "
                   "last_seen, application) VALUES (?, 'ashby', 'x', ?, 'Acme', ?, ?, ?, ?, 'now', 'now', ?)",
                   (f"ashby:x:{i}", str(i), f"Job {i}", f"https://jobs.example.com/{i}", f"fp{i}", status, app))
        db.execute("INSERT INTO scores (job_id, scored_at, match, verdict, key) VALUES (?, 'now', ?, ?, 'k')",
                   (i, 50 + i, verdict))
    db.commit()
    db.close()
    folder = os.path.join(ws.path("applications_dir"), "beta_ml")
    os.makedirs(folder)
    with open(os.path.join(folder, "pending.json"), "w", encoding="utf-8") as f:
        json.dump({"pending": [{"field_id": "f01", "label": "Notice period?", "type": "text", "required": True,
                                "reason": "unknown", "pool_entry": None}]}, f)
    with open(ws.path("applied_log"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "application", "status", "job_url", "form_url"])
        w.writerow(["2026-01-02", "acme_data", "submitted", "https://jobs.example.com/1", ""])
        w.writerow(["2026-01-03", "manual_one", "submitted", "https://other.example.com/9", ""])
        w.writerow(["2026-01-04", "failed_one", "error", "https://other.example.com/8", ""])
    with open(ws.rel("demanded_skills_to_learn.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["skill", "category", "times_requested", "priority"])
        w.writerow(["dbt", "Data", "2", "high"])
        w.writerow(["Airflow", "Data", "5", "high"])
    with open(ws.path("env_file"), "w", encoding="utf-8") as f:
        f.write("TYPESAFE_API_KEY=secret-value\n")
    return ws


def fake_command(argv):
    """Stands in for `jobpilot <argv>`: prints the step; `score` fails, `apply resume` waits a little."""
    code = ("import sys, time; a = sys.argv[1:]; print('ran', ' '.join(a), flush=True); "
            "time.sleep(0.5 if a[:2] == ['apply', 'resume'] else 0); sys.exit(3 if a[0] == 'score' else 0)")
    return [sys.executable, "-I", "-c", code] + argv


def fake_apply(argv):
    """`apply run` reports job 2 as write; with --ids it submits; `apply add` adds job 7; everything else just prints."""
    code = ("import sys; a = sys.argv[1:]; print('ran', ' '.join(a)); "
            "print('ADDED 7  ' + a[2] if a[:2] == ['apply', 'add'] else "
            "'RESULT submitted       2  Acme' if '--ids' in a else "
            "'RESULT write           2  Acme  Job 2  write: CV' if a[:2] == ['apply', 'run'] else '')")
    return [sys.executable, "-I", "-c", code] + argv


def fake_claude(fail=None):
    """Stands in for `claude -p --output-format stream-json`: echoes the prompt's first line as events."""
    events = ("import sys, json; p = sys.stdin.read(); "
              "print(json.dumps({'type': 'system', 'subtype': 'init'})); "
              "print(json.dumps({'type': 'assistant', 'message': {'content': ["
              "{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'python -I build_cv.py x'}}, "
              "{'type': 'text', 'text': 'lucrez'}]}})); ")
    if fail:
        events += f"print(json.dumps({{'type': 'result', 'is_error': True, 'result': {fail!r}}})); sys.exit(1)"
    else:
        events += ("job = 'job ' + p.split('for job ')[1].split()[0] if 'for job ' in p else 'text'; "
                   "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'Gata ' + job + '\\nPDF: output/pdf/CV_A_N_X_EN.pdf', "
                   "'usage': {'input_tokens': 1200, 'output_tokens': 300, 'cache_read_input_tokens': 5000, "
                   "'cache_creation_input_tokens': 500}, 'total_cost_usd': 0.05, 'modelUsage': {'claude-test': {}}}))")
    return lambda: [sys.executable, "-I", "-c", events]


def test_claude_steps():
    ws = make_ws()
    c = TestClient(create_app(ws, Runner(ws, command=fake_apply, claude=fake_claude())))
    check("short description", c.post("/api/tasks", json={"kind": "cv", "text": "too short"}).status_code, 400)
    t = c.post("/api/tasks", json={"kind": "cv", "text": "Data Engineer at Acme\n" + "Python, SQL, Airflow. " * 20}).json()
    check("cv title", t["title"], "CV personalizat — Data Engineer at Acme")
    done = wait(c, t["id"])
    check("cv done", done["status"], "done")
    check("cv result", done["result"], "Gata text\nPDF: output/pdf/CV_A_N_X_EN.pdf")
    check("tool line", "→ Bash python -I build_cv.py x" in done["log"], True)
    t = c.post("/api/tasks", json={"kind": "auto_apply", "dry_run": True}).json()
    done = wait(c, t["id"])
    check("write expands", (done["status"], done["steps"][3:]),
          ("done", ["claude · write job 2", "jobpilot apply run --ids 2 --after-write --dry-run"]))
    check("write result", done["result"].startswith("Gata job 2"), True)
    check("re-run applies", "RESULT submitted" in done["log"], True)
    from dashboard import prompts
    import subprocess
    p = prompts.write_for_job(2, "write: CV")
    check("write prompt has no workspace wrappers", ".claude/skills" in p, False)
    check("write prompt set-cv", f"{prompts.JOBPILOT} apply set-cv 2 <pdf>" in p, True)
    launcher = prompts.JOBPILOT.split('"')[1]
    out = subprocess.run([sys.executable, "-I", launcher, "--help"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    check("launcher runs jobpilot", (out.returncode, "jobpilot - one command" in out.stdout), (0, True))

    ws = make_ws()
    c = TestClient(create_app(ws, Runner(ws, command=fake_apply,
                                         claude=fake_claude("Failed to authenticate: OAuth session expired"))))
    done = wait(c, c.post("/api/tasks", json={"kind": "auto_apply"}).json()["id"])
    check("soft write failure goes on", (done["status"], "RESULT submitted" in done["log"]), ("done", True))
    check("login hint", "nu este autentificat" in done["log"], True)
    done = wait(c, c.post("/api/tasks", json={"kind": "cv", "text": "x" * 300}).json()["id"])
    check("cv fails without login", (done["status"], done["result"]), ("failed", None))
    check("cv file outside pdf dir", c.get("/api/file", params={"path": "ANSWERS_POOL.json"}).status_code, 404)
    os.makedirs(ws.path("pdf_dir"))
    with open(os.path.join(ws.path("pdf_dir"), "CV_A_N_X_EN.pdf"), "wb") as f:
        f.write(b"%PDF-1.4")
    check("cv file", c.get("/api/file", params={"path": "output/pdf/CV_A_N_X_EN.pdf"}).status_code, 200)
    check("no traversal", c.get("/api/file", params={"path": "output/pdf/../../x.pdf"}).status_code, 404)


def test_apply_link():
    ws = make_ws()
    c = TestClient(create_app(ws, Runner(ws, command=fake_apply, claude=fake_claude())))
    pasted = "Data Engineer at Acme\n" + "Python, SQL, Airflow. " * 20
    cv = wait(c, c.post("/api/tasks", json={"kind": "cv", "text": pasted}).json()["id"])
    link = {"kind": "apply_link", "text": "https://jobs.example.com/77", "source_task": cv["id"]}
    check("no PDF on disk → refused", c.post("/api/tasks", json=link).status_code, 400)
    os.makedirs(ws.path("pdf_dir"))
    with open(os.path.join(ws.path("pdf_dir"), "CV_A_N_X_EN.pdf"), "wb") as f:
        f.write(b"%PDF-1.4")
    check("linkedin refused", c.post("/api/tasks", json={**link, "text": "https://www.linkedin.com/jobs/1"}).status_code, 400)
    check("not a url", c.post("/api/tasks", json={**link, "text": "jobs.example.com/77"}).status_code, 400)
    check("source must be a cv task", c.post("/api/tasks", json={**link, "source_task": 999}).status_code, 400)
    t = c.post("/api/tasks", json=link).json()
    check("apply_link title", t["title"], "Aplică la link — Data Engineer at Acme")
    done = wait(c, t["id"])
    check("add then apply run then write", (done["status"], done["steps"]),
          ("done", ["jobpilot apply add https://jobs.example.com/77 --cv output/pdf/CV_A_N_X_EN.pdf --title Data "
                    "Engineer at Acme --description-file output/dashboard_manual/task_1_job.md",
                    "jobpilot apply run --ids 7"]))
    saved = open(ws.rel("output/dashboard_manual/task_1_job.md"), encoding="utf-8").read()
    check("pasted description saved", saved.startswith("Data Engineer at Acme\nPython"), True)
    check("applied", "RESULT submitted" in done["log"], True)
    from dashboard import prompts
    check("cv prompt asks for JOB line", "`JOB: <company> |" in prompts.cv_from_text("x"), True)
    src = runner_mod.REPORT_JOB.findall("Raport\nJOB: Acme Corp | Senior Data Engineer\nPDF: output/pdf/a.pdf")
    check("JOB line parsed", src, [("Acme Corp", "Senior Data Engineer")])


def test_profile_and_credentials():
    ws = make_ws()
    with open(ws.path("answers_pool"), "w", encoding="utf-8") as f:
        json.dump({"screening": {"preferred_work_mode": "Remote only", "salary_expectation_ron_net_month": 1000,
                                 "notice_period": "Immediately"}, "contact": {"city": "Iași"}}, f)
    with open(ws.path("job_sources"), "w", encoding="utf-8") as f:
        json.dump({"boards": {}, "filters": {"title_include": ["data"], "allow_remote": True}}, f)
    with open(ws.path("scoring"), "w", encoding="utf-8") as f:
        json.dump({"target_domain": "data"}, f)
    c = TestClient(create_app(ws, Runner(ws, command=fake_apply, claude=fake_claude())))
    p = c.get("/api/profile").json()
    check("modes from text", (p["workplaces"], p["salary"]["salary_expectation_ron_net_month"]), (["remote"], 1000))
    p = c.put("/api/profile", json={"workplaces": ["remote", "hybrid"], "target_titles": ["Data Analyst", " BI  Developer "],
                                    "salary": {"salary_expectation_ron_net_month": "1200"}, "notice_period": "2 weeks"}).json()
    check("saved profile", (p["preferred_work_mode"], p["target_titles"], p["notice_period"]),
          ("Remote or hybrid", ["Data Analyst", "BI Developer"], "2 weeks"))
    pool = json.load(open(ws.path("answers_pool"), encoding="utf-8"))
    check("salary is a number", pool["screening"]["salary_expectation_ron_net_month"], 1200)
    check("other sections kept", pool["contact"]["city"], "Iași")
    check("utf-8 kept", "Iași" in open(ws.path("answers_pool"), encoding="utf-8").read(), True)
    filters = json.load(open(ws.path("job_sources"), encoding="utf-8"))["filters"]
    check("filters", (filters["workplaces"], filters["allow_remote"], filters["title_include"]),
          (["hybrid", "remote"], True, ["data", "data analyst", "bi developer"]))
    check("onsite only", c.put("/api/profile", json={"workplaces": ["onsite"]}).json()["preferred_work_mode"], "On-site")
    check("no remote → allow_remote off",
          json.load(open(ws.path("job_sources"), encoding="utf-8"))["filters"]["allow_remote"], False)
    check("too many titles", c.put("/api/profile", json={"target_titles": ["a", "b", "c", "d"]}).status_code, 400)
    check("no mode", c.put("/api/profile", json={"workplaces": []}).status_code, 400)
    check("bad salary", c.put("/api/profile", json={"salary": {"salary_expectation_eur_net_month": "lots"}}).status_code, 400)

    r = c.post("/api/credentials", json={"name": "ANTHROPIC_API_KEY", "value": "sk-test"}).json()
    check("key set", r["set"], True)
    env = open(ws.path("env_file"), encoding="utf-8").read()
    check(".env keeps other keys", ("TYPESAFE_API_KEY=secret-value" in env, "ANTHROPIC_API_KEY=sk-test" in env), (True, True))
    c.post("/api/credentials", json={"name": "ANTHROPIC_API_KEY", "value": ""})
    check("key removed", "ANTHROPIC" in open(ws.path("env_file"), encoding="utf-8").read(), False)
    check("unknown credential", c.post("/api/credentials", json={"name": "PATH", "value": "x"}).status_code, 400)
    check("multi-line key", c.post("/api/credentials", json={"name": "TYPESAFE_API_KEY", "value": "a\nb"}).status_code, 400)

    check("login needs a url", c.post("/api/tasks", json={"kind": "login", "text": "acme"}).status_code, 400)
    check("login no linkedin", c.post("/api/tasks", json={"kind": "login", "text": "https://www.linkedin.com/x"}).status_code, 400)


def test_competency_revert():
    ws = make_ws()
    pool = ws.path("skills_pool")
    with open(pool, "w", encoding="utf-8") as f:
        f.write("# Pool\n- Python\n")
    edit = ("import sys; sys.stdin.read(); open(sys.argv[1], 'a', encoding='utf-8').write('- dbt (B)\\n'); "
            "import json; print(json.dumps({'type': 'result', 'is_error': False, 'result': 'Adăugat dbt'}))")
    c = TestClient(create_app(ws, Runner(ws, command=fake_apply, claude=lambda: [sys.executable, "-I", "-c", edit, pool])))
    check("short competency", c.post("/api/tasks", json={"kind": "competency", "text": "dbt"}).status_code, 400)
    done = wait(c, c.post("/api/tasks", json={"kind": "competency", "text": "Am construit un proiect dbt pe Postgres"}).json()["id"])
    check("diff in result", ("+- dbt (B)" in done["result"], done["result"].startswith("Adăugat dbt")), (True, True))
    check("revert", c.post(f"/api/tasks/{done['id']}/revert").status_code, 200)
    check("pool restored", open(pool, encoding="utf-8").read(), "# Pool\n- Python\n")
    check("revert twice", c.post(f"/api/tasks/{done['id']}/revert").status_code, 400)
    done = wait(c, c.post("/api/tasks", json={"kind": "competency", "text": "Am construit un proiect dbt pe Postgres"}).json()["id"])
    with open(pool, "a", encoding="utf-8") as f:
        f.write("- edited by hand\n")
    check("no revert over later edits", c.post(f"/api/tasks/{done['id']}/revert").status_code, 400)


def test_mirel():
    ws = make_ws()
    folder = os.path.join(ws.path("applications_dir"), "beta_ml")
    with open(os.path.join(folder, "form.json"), "w", encoding="utf-8") as f:
        json.dump({"fields": [{"field_id": "f01", "label": "Notice period?", "type": "text", "required": True}]}, f)
    with open(ws.path("answers_pool"), "w", encoding="utf-8") as f:
        json.dump({"settings": {}, "question_bank": []}, f)
    db = store.connect(ws.path("db"))
    db.execute("UPDATE jobs SET reason = 'unknown: Notice period? → python -m jobpilot apply resume 4' WHERE id = 4")
    db.commit()
    db.close()
    # the fake Claude stores the answer the way add-answer would, and reports a session id
    script = ("import sys, json; p = sys.stdin.read(); argv = sys.argv[1:]; "
              "pool = json.load(open(argv[0], encoding='utf-8')); "
              "store = '2 saptamani' in p; "
              "store and pool['question_bank'].append({'id': 'notice', 'patterns': ['notice'], 'answer': '2 weeks'}); "
              "json.dump(pool, open(argv[0], 'w', encoding='utf-8')); "
              "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 'S1'})); "
              "resumed = '--resume' in argv; "
              "print(json.dumps({'type': 'result', 'is_error': False, 'session_id': 'S1', "
              "'result': ('Salvat.' if store else 'Salut! Care e preavizul?') + (' (resumed)' if resumed else '')}))")
    fake = lambda: [sys.executable, "-I", "-c", script, ws.path("answers_pool")]
    c = TestClient(create_app(ws, Runner(ws, command=fake_apply, claude=fake)))
    state = c.get("/api/mirel").json()
    check("questions", [(q["label"], [a["job_id"] for a in q["applications"]]) for q in state["questions"]],
          [("Notice period?", [4])])

    def turn(body):
        check("turn accepted", c.post("/api/mirel", json=body).status_code, 200)
        deadline = time.time() + 20
        while time.time() < deadline and c.get("/api/mirel").json()["busy"]:
            time.sleep(0.05)
        return c.get("/api/mirel").json()

    state = turn({})
    check("opening", [(m["role"], m["text"]) for m in state["messages"]], [("mirel", "Salut! Care e preavizul?")])
    state = turn({"text": "2 saptamani"})
    check("reply resumes the session", state["messages"][-2]["text"], "Salvat. (resumed)")
    check("job unblocked note", state["messages"][-1]["role"], "system")
    check("questions answered", state["questions"], [])
    check("job back to form_ready", c.get("/api/jobs/4").json()["status"], "form_ready")
    answers = json.load(open(os.path.join(folder, "answers.json"), encoding="utf-8"))["answers"]
    check("form re-matched", answers.get("f01"), "2 weeks")
    check("reset", c.post("/api/mirel/reset").status_code, 200)
    check("history cleared", c.get("/api/mirel").json()["messages"], [])


def test_notifications():
    ws = make_ws()
    c = TestClient(create_app(ws, Runner(ws, command=fake_apply, claude=fake_claude()), port=9999))
    done = wait(c, c.post("/api/tasks", json={"kind": "auto_apply"}).json()["id"])
    time.sleep(0.3)
    n = c.get("/api/notifications").json()
    check("run summary", [(x["kind"], x["body"]) for x in n["notifications"]], [("run", "1 trimise")])
    check("toast sent", TOASTS[-1], ("Aplicarea automată s-a terminat", "1 trimise", f"http://127.0.0.1:9999/#task={done['id']}"))
    db = store.connect(ws.path("db"))
    db.execute("UPDATE jobs SET status = 'needs_human', reason = 'CAPTCHA on the page' WHERE id = 3")
    db.commit()
    db.close()
    wait(c, c.post("/api/tasks", json={"kind": "plan"}).json()["id"])
    time.sleep(0.3)
    n = c.get("/api/notifications").json()
    check("needs input", [(x["kind"], x["job_id"], x["body"]) for x in n["notifications"]][0],
          ("needs_input", 3, "CAPTCHA on the page"))
    check("job 4 was already waiting", [x["job_id"] for x in n["notifications"]].count(4), 0)
    check("unread", n["unread"], 2)
    check("read one", c.post("/api/notifications/read", json={"ids": [n["notifications"][0]["id"]]}).json()["unread"], 1)
    check("read all", c.post("/api/notifications/read", json={}).json()["unread"], 0)
    c.put("/api/notifications/settings", json={"toasts": False})
    before = len(TOASTS)
    wait(c, c.post("/api/tasks", json={"kind": "cv", "text": "x" * 300}).json()["id"])
    time.sleep(0.3)
    check("cv note without toast", (c.get("/api/notifications").json()["notifications"][0]["kind"], len(TOASTS)),
          ("cv", before))
    check("bad schedule time", c.put("/api/schedule", json={"time": "25:00"}).status_code, 400)
    check("summary keeps the last result per job", notify_mod.auto_apply_summary(
        "RESULT write   2  x\nRESULT needs_human 5 y\nRESULT submitted   2  x\n"), {"submitted": 1, "needs_human": 1})


def wait(c, task_id, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        task = c.get(f"/api/tasks/{task_id}").json()
        if task["status"] not in ("queued", "running"):
            return task
        time.sleep(0.05)
    return task


def locking_command(db_path):
    """A child step that holds the db write lock while it prints (as `apply run` did during a browser fill)."""
    code = ("import sys, time, sqlite3; db = sqlite3.connect(sys.argv[1], isolation_level=None); "
            "db.execute('BEGIN IMMEDIATE'); print('locked', flush=True); time.sleep(0.6); "
            "print('still locked', flush=True); time.sleep(0.6); db.execute('COMMIT'); print('released', flush=True)")
    return lambda argv: [sys.executable, "-I", "-c", code, db_path]


def test_locked_db():
    ws = make_ws()
    runner = Runner(ws, command=locking_command(ws.path("db")))
    runner.busy_ms = 100   # far shorter than the child's lock
    c = TestClient(create_app(ws, runner))
    first = wait(c, c.post("/api/tasks", json={"kind": "plan"}).json()["id"])
    check("locked db: task still finishes", first["status"], "done")
    check("locked db: no log line lost", all(x in first["log"] for x in ("locked", "still locked", "released")), True)
    second = wait(c, c.post("/api/tasks", json={"kind": "collect"}).json()["id"])
    check("locked db: worker survives for the next task", second["status"], "done")


def test_tasks():
    ws = make_ws()
    c = TestClient(create_app(ws, Runner(ws, command=fake_command)))
    t = c.post("/api/tasks", json={"kind": "plan"}).json()
    done = wait(c, t["id"])
    check("plan done", done["status"], "done")
    check("plan log", "ran apply plan" in done["log"], True)
    t = c.post("/api/tasks", json={"kind": "auto_apply", "dry_run": True}).json()
    check("dry-run steps", t["steps"], ["jobpilot collect run", "jobpilot score score", "jobpilot apply run --dry-run"])
    done = wait(c, t["id"])
    check("stops at failed step", (done["status"], done["step"]), ("failed", 1))
    check("later step not run", "apply run" in done["log"].split("score score", 1)[1], False)
    first = c.post("/api/tasks", json={"kind": "resume", "job_id": 4}).json()
    again = c.post("/api/tasks", json={"kind": "resume", "job_id": 4}).json()
    check("same task while running", again["id"], first["id"])
    queued = c.post("/api/tasks", json={"kind": "collect"}).json()
    check("cancel queued", c.post(f"/api/tasks/{queued['id']}/cancel").json()["status"], "cancelled")
    check("resume done", wait(c, first["id"])["status"], "done")
    check("resume needs open job", c.post("/api/tasks", json={"kind": "resume", "job_id": 1}).status_code, 400)
    check("unknown kind", c.post("/api/tasks", json={"kind": "rm"}).status_code, 400)
    check("cross-site refused", c.post("/api/tasks", json={"kind": "plan"},
                                       headers={"Origin": "https://evil.example.com"}).status_code, 403)
    check("foreign host refused", c.get("/api/overview", headers={"Host": "evil.example.com"}).status_code, 403)
    check("history", [t["kind"] for t in c.get("/api/tasks").json()][:2], ["collect", "resume"])
    state = c.get("/api/apply-state").json()
    check("apply state", (state["submit"], state["daily_cap"], state["remaining"]), (False, None, None))  # no cap by default
    check("top_n saved", c.put("/api/apply-state", json={"top_n": 7}).json()["top_n"], 7)
    check("top_n out of range", c.put("/api/apply-state", json={"top_n": 0}).status_code, 400)


def test_api():
    ws = make_ws()
    c = TestClient(create_app(ws, Runner(ws, command=fake_command)))
    check("cards", c.get("/api/overview").json()["cards"], {"applied": 2, "in_progress": 1, "needs_input": 1})
    applied = c.get("/api/jobs", params={"category": "applied"}).json()
    check("applied newest first", [(j["id"], j["application"]) for j in applied], [(None, "manual_one"), (1, "acme_data")])
    check("in progress", [j["id"] for j in c.get("/api/jobs", params={"category": "in_progress"}).json()], [2])
    check("needs input", [j["id"] for j in c.get("/api/jobs", params={"category": "needs_input"}).json()], [4])
    detail = c.get("/api/jobs/4").json()
    check("pending", [p["field_id"] for p in detail["pending"]], ["f01"])
    check("score", detail["score"]["verdict"], "apply")
    check("no prefill", c.get("/api/jobs/4/prefill.png").status_code, 404)
    check("unknown job", c.get("/api/jobs/99").status_code, 404)
    check("unknown category", c.get("/api/jobs", params={"category": "x"}).status_code, 400)
    skills = c.get("/api/skills").json()
    check("skills order", [s["skill"] for s in skills["skills"]], ["Airflow", "dbt"])
    check("skills counts", (skills["rows"], skills["ungrouped"]), (2, 2))
    creds = c.get("/api/credentials")
    check("key set", creds.json()["TYPESAFE_API_KEY"], True)
    check("value never sent", "secret-value" in creds.text, False)


def test_usage():
    ws = make_ws()
    c = TestClient(create_app(ws, Runner(ws, command=fake_apply, claude=fake_claude())))
    empty = c.get("/api/usage").json()
    check("no Claude runs yet", empty["periods"]["today"]["claude"]["runs"], 0)
    check("30 days of series", len(empty["series"]), 30)
    t = c.post("/api/tasks", json={"kind": "cv", "text": "Data Engineer at Acme\n" + "Python, SQL, Airflow. " * 20}).json()
    done = wait(c, t["id"])
    check("token line in the log", "tokeni: in 1.2k · out 300 · cache 5.5k · ~$0.05 (echivalent API)" in done["log"], True)
    check("task usage", done["usage"], {"tokens": 7000, "cost_usd": 0.05, "runs": 1})
    check("task list usage", c.get("/api/tasks").json()[0]["usage"]["tokens"], 7000)
    u = c.get("/api/usage").json()
    today = u["periods"]["today"]["claude"]
    check("today", (today["tokens"], today["input"], today["output"], today["cache"], today["runs"]),
          (7000, 1200, 300, 5500, 1))
    check("by kind", [(k["kind"], k["tokens"]) for k in u["by_kind"]], [("cv", 7000)])
    check("top task", (u["top_tasks"][0]["task_id"], u["top_tasks"][0]["title"].startswith("CV personalizat")),
          (t["id"], True))
    check("series ends today", u["series"][-1]["claude"], 7000)
    t = c.post("/api/tasks", json={"kind": "auto_apply", "dry_run": True}).json()
    wait(c, t["id"])
    kinds = {k["kind"]: k["runs"] for k in c.get("/api/usage").json()["by_kind"]}
    check("auto-apply write steps count apart", kinds, {"cv": 1, "write": 1})
    db = store.connect(ws.path("db"))
    db.execute("UPDATE scores SET scored_at = ?, input_tokens = 6000 WHERE job_id = 2", (store.now_utc(),))
    db.commit()
    db.close()
    check("Jev tokens", c.get("/api/usage").json()["periods"]["today"]["jev"], {"tokens": 6000, "jobs": 1})


def test_cluster_skills():
    ws = make_ws()
    with open(ws.rel("demanded_skills_to_learn.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["skill", "category", "times_requested", "jobs", "priority"])
        w.writerow(["LLM orchestration", "AI", "2", "A - AI | B - AI", "high"])
        w.writerow(["LangChain / LangGraph", "AI", "2", "B - AI | C - AI", "high"])
        w.writerow(["Tableau", "BI", "1", "D - BI", "low"])
    # the fake Claude groups what it is sent the way the prompt asks, and reports tokens
    script = ("import sys, json; p = sys.stdin.read(); "
              "names = json.loads(p.rsplit('<new_names>', 1)[1].split('</new_names>')[0]); "
              "groups = {'LLM frameworks': [n for n in names if 'L' in n[:2]]}; "
              "groups.update({n: [] for n in names if n not in groups['LLM frameworks']}); "
              "json.dump(groups, open('skill_clusters.json', 'w', encoding='utf-8')); "
              "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'Am grupat ' + str(len(names)), "
              "'usage': {'input_tokens': 10, 'output_tokens': 5}}))")
    c = TestClient(create_app(ws, Runner(ws, command=fake_command,
                                         claude=lambda: [sys.executable, "-I", "-c", script])))
    check("three to group", c.get("/api/skills").json()["ungrouped"], 3)
    t = c.post("/api/tasks", json={"kind": "cluster_skills"}).json()
    check("title", t["title"], "Grupează skill-urile cerute — 3 nume noi")
    done = wait(c, t["id"])
    check("done", (done["status"], done["result"].startswith("Am grupat 3")), ("done", True))
    s = c.get("/api/skills").json()
    check("grouped, distinct ads", [(r["skill"], r["times_requested"]) for r in s["skills"]],
          [("LLM frameworks", 3), ("Tableau", 1)])
    check("nothing left to group", s["ungrouped"], 0)
    check("no second run", c.post("/api/tasks", json={"kind": "cluster_skills"}).status_code, 400)
    check("cost counted", [k["kind"] for k in c.get("/api/usage").json()["by_kind"]], ["cluster_skills"])
    check("revert removes the new file", c.post(f"/api/tasks/{t['id']}/revert").json()["message"],
          "skill_clusters.json restored")
    check("file gone", os.path.exists(ws.path("skill_clusters")), False)


FAKE_FACTS = {"name": "Maria Ionescu", "headline": "Data Analyst", "contact": {"email": "maria@example.com", "city": "Cluj"},
              "roles": [{"title": "Data Analyst", "employer": "Example SRL", "start": "2022", "end": "Present"}],
              "education": [], "certifications": [], "languages": [{"language": "English", "level": "C1"}],
              "skills": [{"name": "SQL", "category": "Data", "level": "A", "evidence": "Example SRL"}],
              "projects": [], "target_roles_guess": ["Data Analyst"], "cv_languages": ["EN"],
              "open_questions": ["How many reports a month?"], "confirmed": False}


def onboarding_claude():
    """A fake Claude for the onboarding: extract writes facts.json, write writes the pool, the guide answers."""
    facts = json.dumps(FAKE_FACTS)
    script = ("import sys, json, os; p = sys.stdin.read(); argv = sys.argv[1:]; "
              "guide = '--append-system-prompt' in argv and 'onboarding guide' in argv[argv.index('--append-system-prompt') + 1]; "
              "w = lambda f, t: open(f, 'w', encoding='utf-8').write(t); "
              f"extract = 'write `onboarding/facts.json`' in p; extract and w('onboarding/facts.json', {facts!r}); "
              "write = 'Do step 5' in p; "
              "write and w('MASTER_SKILLS_POOL.md', '# Maria Ionescu - Master Skills Pool\\n- [A] SQL\\n'); "
              "guide and w('onboarding/enrichment.md', '# Enrichment\\n- 40 reports a month\\n'); "
              "reply = 'Întrebarea 1: câte rapoarte?' if guide else 'Gata'; "
              "print(json.dumps({'type': 'system', 'session_id': 's1'})); "
              "print(json.dumps({'type': 'result', 'is_error': False, 'result': reply, 'session_id': 's1', "
              "'usage': {'input_tokens': 100, 'output_tokens': 20}}))")
    return lambda: [sys.executable, "-I", "-c", script]


def fake_docx():
    import base64
    import io
    import docx
    d = docx.Document()
    d.add_paragraph("Maria Ionescu - Data Analyst, Cluj, maria@example.com")
    for i in range(12):
        d.add_paragraph(f"Example SRL 2022 - Present: built SQL reports and dashboards for the finance team, part {i}.")
    buf = io.BytesIO()
    d.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


def test_onboarding():
    try:
        content = fake_docx()
    except ImportError:
        print("SKIP onboarding upload: python-docx not installed")
        return
    ws = make_ws()
    with open(ws.path("answers_pool"), "w", encoding="utf-8") as f:   # what `jobpilot init` creates
        json.dump({"settings": {"auto_submit": False}, "contact": {}, "screening": {},
                   "voluntary_disclosures": {}, "consents": [{"id": "this_application_data", "patterns": ["privacy"],
                                                              "answer": None}], "question_bank": []}, f)
    c = TestClient(create_app(ws, Runner(ws, command=fake_command, claude=onboarding_claude())))
    st = c.get("/api/onboarding").json()
    check("new workspace needs onboarding", (st["needs_onboarding"], st["steps"]["cv"]), (True, False))
    check("write refused before the CV", c.post("/api/tasks", json={"kind": "onboard_write"}).status_code, 400)
    check("not a CV", c.post("/api/onboarding/cv", json={"filename": "cv.txt", "content": "eA=="}).status_code, 400)
    check("broken upload", c.post("/api/onboarding/cv", json={"filename": "cv.docx", "content": "%%%"}).status_code, 400)
    st = c.post("/api/onboarding/cv", json={"filename": "Maria CV.docx", "content": content,
                                            "extra_text": "LinkedIn: SQL, Power BI"}).json()
    check("cv saved and read", (st["steps"]["cv"], st["cv"]["name"], st["cv"]["text"], st["cv"]["chars"] > 200),
          (True, "cv.docx", True, True))
    t = c.post("/api/tasks", json={"kind": "onboard_extract"}).json()
    done = wait(c, t["id"])
    check("extract done", (done["status"], done["title"]), ("done", "Onboarding — citesc CV-ul"))
    st = c.get("/api/onboarding").json()
    check("facts proposed, not confirmed", (st["steps"]["facts"], st["steps"]["confirmed"]), (True, False))
    check("form prefilled from the CV", (st["answers"]["contact"]["email"], st["answers"]["contact"]["first_name"],
                                         st["answers"]["screening"]["current_company"]),
          ("maria@example.com", "Maria", "Example SRL"))
    bad = {**st["facts"], "skills": [{"name": "SQL", "level": "Z"}]}
    check("bad level refused", c.put("/api/onboarding/facts", json=bad).status_code, 400)
    facts = {**st["facts"], "skills": [{"name": "SQL", "category": "Data", "level": "b", "evidence": "x"}]}
    st = c.put("/api/onboarding/facts", json=facts).json()
    check("confirmed, level normalized", (st["steps"]["confirmed"], st["facts"]["skills"][0]["level"]), (True, "B"))
    answers = {**st["answers"], "cv_languages": ["EN", "RO"], "consents": {"this_application_data": True},
               "profile": {**st["answers"]["profile"], "workplaces": ["remote"], "target_titles": ["Data Analyst"]},
               "target_domain": "data analysis and BI"}
    check("email needed", c.put("/api/onboarding/answers", json={**answers, "contact": {"email": ""}}).status_code, 400)
    st = c.put("/api/onboarding/answers", json=answers).json()
    pool = json.load(open(ws.path("answers_pool"), encoding="utf-8"))
    check("answers in the pool", (pool["contact"]["email"], pool["screening"]["preferred_work_mode"],
                                  pool["consents"][0]["answer"], pool["voluntary_disclosures"]["gender"]),
          ("maria@example.com", "Remote only", True, "Prefer not to say"))
    check("auto-submit untouched", pool["settings"]["auto_submit"], False)
    scoring = json.load(open(ws.path("scoring"), encoding="utf-8"))
    check("target titles and domain", (scoring["target_titles"], scoring["target_domain"]),
          (["Data Analyst"], "data analysis and BI"))
    check("write needs the questions step", c.post("/api/tasks", json={"kind": "onboard_write"}).status_code, 400)
    c.post("/api/guide", json={})
    deadline = time.time() + 20
    while time.time() < deadline and (c.get("/api/guide").json()["busy"] or len(c.get("/api/guide").json()["messages"]) < 1):
        time.sleep(0.05)
    g = c.get("/api/guide").json()
    check("guide asks", [m["text"] for m in g["messages"]], ["Întrebarea 1: câte rapoarte?"])
    check("Mirel's chat stays apart", c.get("/api/mirel").json()["messages"], [])
    check("enrichment written by the guide", c.get("/api/onboarding").json()["steps"]["enrichment"], True)
    t = c.post("/api/tasks", json={"kind": "onboard_write"}).json()
    done = wait(c, t["id"])
    st = c.get("/api/onboarding").json()
    check("written", (done["status"], st["steps"]["written"], st["pool_exists"], st["needs_onboarding"]),
          ("done", True, True, False))
    check("onboarding tokens counted", sorted(k["kind"] for k in c.get("/api/usage").json()["by_kind"]),
          ["onboard_extract", "onboard_write", "onboarding"])
    check("revert removes the new pool", c.post(f"/api/tasks/{t['id']}/revert").status_code, 200)
    check("pool gone", os.path.exists(ws.path("skills_pool")), False)
    ws2 = make_ws()
    c2 = TestClient(create_app(ws2, Runner(ws2, command=fake_command, claude=onboarding_claude())))
    check("skip the questions", c2.post("/api/onboarding/skip-questions").json()["steps"]["enrichment"], True)


if __name__ == "__main__":
    test_api()
    test_usage()
    test_cluster_skills()
    test_onboarding()
    test_tasks()
    test_locked_db()
    test_claude_steps()
    test_apply_link()
    test_profile_and_credentials()
    test_competency_revert()
    test_mirel()
    test_notifications()
    if FAILS:
        print("\n".join(FAILS))
        print(f"FAILED {len(FAILS)}")
        sys.exit(1)
    print("OK dashboard")
