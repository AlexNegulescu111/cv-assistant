"""Offline tests for `jobpilot init`: a new workspace gets every config, the skills and a checklist; a second run
keeps everything (never overwrites).

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import init as init_mod  # noqa: E402
from jobpilot.workspace import Workspace  # noqa: E402

FAILS = []
init_mod._claude_status = lambda: (True, False)   # no real CLI call in tests


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = init_mod.init(argv)
    return code, buf.getvalue()


def test_init():
    root = os.path.join(tempfile.mkdtemp(), "Aplicari")
    code, out = run(["--workspace", root])
    ws = Workspace(root)
    check("exit", code, 0)
    for key in ("job_sources", "scoring", "apply_config", "answers_pool"):
        check(f"{key} created", os.path.exists(ws.path(key)), True)
    for key in ("drafts_dir", "pdf_dir", "applications_dir"):
        check(f"{key} folder", os.path.isdir(ws.path(key)), True)
    check("onboarding folder", os.path.isdir(ws.rel("onboarding")), True)
    apply_cfg = json.load(open(ws.path("apply_config"), encoding="utf-8"))
    check("safe defaults for a new user", (apply_cfg["submit"], apply_cfg["daily_cap"]), (False, 10))
    pool = json.load(open(ws.path("answers_pool"), encoding="utf-8"))
    check("answers pool: auto-submit off", pool["settings"]["auto_submit"], False)
    check("answers pool: empty contact", pool["contact"]["email"], "")
    skill = os.path.join(root, ".claude", "skills", "setup-profile", "SKILL.md")
    check("skills installed", os.path.exists(skill), True)
    check("no pycache copied", any("__pycache__" in d for _, ds, _ in os.walk(os.path.join(root, ".claude"))
                                   for d in ds), False)
    check("onboarding is next", "tab „Început”" in out, True)
    check("checklist", "checklist:" in out and "Claude Code signed in" in out, True)
    check("pool not written by init", os.path.exists(ws.path("skills_pool")), False)

    with open(ws.path("answers_pool"), "w", encoding="utf-8") as f:
        json.dump({"contact": {"email": "maria@example.com"}}, f)
    code, out = run(["--workspace", root])
    check("second run keeps every config", out.count("KEEP    ") >= 4 and "CREATED" not in out, True)
    check("answers kept", json.load(open(ws.path("answers_pool"), encoding="utf-8")), {"contact": {"email": "maria@example.com"}})
    check("skills kept", "skills already in" in out, True)
    code, out = run(["--workspace", os.path.join(root, "other"), "--no-skills"])
    check("--no-skills", os.path.exists(os.path.join(root, "other", ".claude")), False)


def test_templates_in_sync():
    """The onboarding templates in jobpilot/templates are copies of the skills' templates (build_bundles.py)."""
    pkg = init_mod.PACKAGE_ROOT
    for name, skill in (("ANSWERS_POOL.template.json", "add-answer"), ("MASTER_SKILLS_POOL.template.md", "setup-profile"),
                        ("profile.template.json", "setup-profile")):
        a = open(os.path.join(pkg, "skills", skill, "templates", name), encoding="utf-8").read()
        b = open(os.path.join(init_mod.TEMPLATES, name), encoding="utf-8").read()
        check(f"{name} in sync (run tools/build_bundles.py)", a == b, True)


TESTS = (test_init, test_templates_in_sync)

if __name__ == "__main__":
    for t in TESTS:
        t()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all init tests passed")
