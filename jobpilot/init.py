"""`jobpilot init`: a new workspace, ready for onboarding. Never overwrites a file that exists (KEEP).

    python -m jobpilot init [--workspace DIR] [--no-skills] [--open]

Creates the example configs (job_sources.json, scoring.json, apply.json - auto-submit off, 10 a day), an empty
ANSWERS_POOL.json (auto_submit false), drafts/ output/ onboarding/, and copies the cv-assistant skills into
<workspace>/.claude/skills so the dashboard's Claude steps (run in the workspace) find them. MASTER_SKILLS_POOL.md
and profile.json are written by the onboarding (dashboard tab "Început": CV → facts → answers → questions → files).
Then a checklist: Python, python-docx / pypdf, Word, Playwright + Chromium, Claude Code CLI, TYPESAFE_API_KEY.
--open starts the dashboard on the workspace.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from .workspace import DEFAULTS, Workspace

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
SKILLS = os.path.join(PACKAGE_ROOT, "skills")   # present in a cloned repo / editable install
INIT_FILES = {"job_sources": "job_sources.example.json", "scoring": "scoring.example.json",
              "apply_config": "apply.example.json", "answers_pool": "ANSWERS_POOL.template.json"}
FOLDERS = ("drafts_dir", "pdf_dir", "applications_dir")
SKILL_DIR = os.path.join(".claude", "skills")


def install_skills(dest_root, force=False, source=SKILLS):
    """Copies every skill folder of the package into dest_root → (installed, kept) names."""
    os.makedirs(dest_root, exist_ok=True)
    installed, kept = [], []
    for name in sorted(os.listdir(source)):
        src, dst = os.path.join(source, name), os.path.join(dest_root, name)
        if not os.path.isfile(os.path.join(src, "SKILL.md")):
            continue
        if os.path.exists(dst):
            if not force:
                kept.append(name)
                continue
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        installed.append(name)
    return installed, kept


def _module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _word_installed():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"))
        return True
    except OSError:
        return False


def _chromium_installed():
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
    return os.path.isdir(base) and any(d.startswith("chromium") for d in os.listdir(base))


def _claude_status():
    exe = shutil.which("claude")
    if not exe:
        return False, False
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        return True, bool(json.loads(r.stdout or "{}").get("loggedIn"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return True, False


def checklist(ws):
    """→ [(ok, what, how to fix)] for everything the pipeline and the dashboard need."""
    claude, logged_in = _claude_status()
    return [
        (sys.version_info >= (3, 10), f"Python {sys.version.split()[0]} (3.10+)", "install Python 3.10 or newer"),
        (_module("docx") and _module("pypdf"), "python-docx + pypdf (CV DOCX, reading the CV at onboarding)",
         'pip install "./cv-assistant[cv]"'),
        (_word_installed(), "Microsoft Word (DOCX → PDF)", "install Word, or export the DOCX to PDF by hand"),
        (_module("fastapi") and _module("uvicorn"), "FastAPI + Uvicorn (the dashboard)",
         'pip install "./cv-assistant[dashboard]"'),
        (_module("playwright"), "Playwright (reading and filling application forms)",
         'pip install "./cv-assistant[browser]"'),
        (_chromium_installed(), "Chromium for Playwright", "python -m playwright install chromium"),
        (claude, "Claude Code CLI (CVs, texts, onboarding)", "install Claude Code: https://claude.com/claude-code"),
        (logged_in, "Claude Code signed in", "run `claude` in a terminal, then /login"),
        (bool(ws.env("TYPESAFE_API_KEY")), "TYPESAFE_API_KEY (Jev scoring)",
         "create a key at https://console.typesafe.ai/keys, then the dashboard's Credențiale tab"),
    ]


def init(argv):
    ap = argparse.ArgumentParser(prog="jobpilot init", description=__doc__.split("\n")[0])
    ap.add_argument("--workspace")
    ap.add_argument("--no-skills", action="store_true", help="do not copy the skills into <workspace>/.claude/skills")
    ap.add_argument("--open", action="store_true", help="start the dashboard on the workspace afterwards")
    a = ap.parse_args(argv)
    ws = Workspace(a.workspace)
    os.makedirs(ws.root, exist_ok=True)
    for key, template in INIT_FILES.items():
        dst = ws.path(key)
        if os.path.exists(dst):
            print(f"KEEP    {dst}")
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(os.path.join(TEMPLATES, template), dst)
        print(f"CREATED {dst}")
    for key in FOLDERS:
        os.makedirs(ws.path(key), exist_ok=True)
    os.makedirs(ws.rel("onboarding"), exist_ok=True)
    if not a.no_skills:
        if os.path.isdir(SKILLS):
            installed, kept = install_skills(os.path.join(ws.root, SKILL_DIR))
            for name in installed:
                print(f"CREATED {os.path.join(ws.root, SKILL_DIR, name)}")
            if kept:
                print(f"KEEP    skills already in {os.path.join(ws.root, SKILL_DIR)}: {', '.join(kept)}")
        else:
            print(f"NOTE    no skills folder next to the package ({SKILLS}); install from a cloned repo "
                  "(pip install -e ./cv-assistant) or run tools/install.py --target claude --project <workspace>")
    for key in ("skills_pool",):
        if not os.path.exists(ws.path(key)):
            print(f"NEXT    {ws.path(key)} is written by the onboarding: dashboard → tab „Început” (CV → întrebări)")
    print("\nchecklist:")
    for ok, what, fix in checklist(ws):
        print(f"  {'OK ' if ok else '-- '} {what}" + ("" if ok else f"  → {fix}"))
    print(f"\npaths: {', '.join(f'{k}={ws.path(k)}' for k in DEFAULTS)}")
    if a.open:
        try:
            from dashboard.__main__ import main as dashboard_main
        except ImportError:
            print('the dashboard is not installed: pip install "./cv-assistant[dashboard]"')
            return 1
        return dashboard_main(["--workspace", ws.root])
    print(f'\nnext: python -m dashboard --workspace "{ws.root}"  (or jobpilot-dashboard), tab „Început”')
    return 0
