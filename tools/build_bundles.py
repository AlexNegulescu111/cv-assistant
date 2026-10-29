"""Build the distributable bundles from skills/ (the single source of truth).

Usage (from anywhere):  python tools/build_bundles.py

Writes:
    chatgpt/knowledge/              flat files to upload as Custom GPT / Project knowledge
    dist/claude-skills/<name>.zip   one zip per skill, for claude.ai → Settings → Capabilities → Skills
    dist/test_workspace.zip         fictional workspace for tests/PARITY_TEST.md
    dist/cv-assistant.zip           the whole package (Claude Code plugin + ChatGPT files + README)
    jobpilot/templates/             copies of the onboarding templates `jobpilot init` / the dashboard use
"""
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
KNOWLEDGE = os.path.join(ROOT, "chatgpt", "knowledge")
DIST = os.path.join(ROOT, "dist")
GPT_LIMIT = 8000
SKIP_DIRS = {"__pycache__", "dist", ".git", "_test_ws", "node_modules"}


def build_knowledge():
    shutil.rmtree(KNOWLEDGE, ignore_errors=True)
    os.makedirs(KNOWLEDGE)
    names = []
    for skill in sorted(os.listdir(SKILLS)):
        base = os.path.join(SKILLS, skill)
        shutil.copy(os.path.join(base, "SKILL.md"), os.path.join(KNOWLEDGE, f"playbook_{skill}.md"))
        names.append(f"playbook_{skill}.md")
        for sub in ("scripts", "templates"):
            folder = os.path.join(base, sub)
            if os.path.isdir(folder):
                for f in sorted(os.listdir(folder)):
                    if f.endswith((".py", ".md", ".json", ".csv", ".html")):
                        if os.path.exists(os.path.join(KNOWLEDGE, f)):
                            sys.exit(f"name clash in knowledge: {f}")
                        shutil.copy(os.path.join(folder, f), KNOWLEDGE)
                        names.append(f)
    for f in os.listdir(os.path.join(ROOT, "examples")):
        shutil.copy(os.path.join(ROOT, "examples", f), KNOWLEDGE)
        names.append(f)
    if len(names) > 20:
        sys.exit(f"{len(names)} knowledge files; a Custom GPT accepts at most 20")
    print(f"chatgpt/knowledge: {len(names)} files")


PACKAGE_TEMPLATES = {   # jobpilot/templates/<name> ← skills/<skill>/templates/<name> (skills/ stays the source)
    "ANSWERS_POOL.template.json": "add-answer",
    "MASTER_SKILLS_POOL.template.md": "setup-profile",
    "profile.template.json": "setup-profile",
}


def copy_templates():
    dst = os.path.join(ROOT, "jobpilot", "templates")
    for name, skill in PACKAGE_TEMPLATES.items():
        shutil.copy(os.path.join(SKILLS, skill, "templates", name), os.path.join(dst, name))
    print(f"jobpilot/templates: {len(PACKAGE_TEMPLATES)} onboarding templates copied")


def zip_dir(src, zpath, arc_root):
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                full = os.path.join(folder, f)
                z.write(full, os.path.join(arc_root, os.path.relpath(full, src)))


def build_zips():
    shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(os.path.join(DIST, "claude-skills"))
    for skill in sorted(os.listdir(SKILLS)):
        zip_dir(os.path.join(SKILLS, skill), os.path.join(DIST, "claude-skills", f"{skill}.zip"), skill)
    zip_dir(os.path.join(ROOT, "tests", "fixture_workspace"), os.path.join(DIST, "test_workspace.zip"), "")
    tmp = os.path.join(os.path.dirname(ROOT), "cv-assistant.zip")
    zip_dir(ROOT, tmp, "cv-assistant")
    shutil.move(tmp, os.path.join(DIST, "cv-assistant.zip"))
    print("dist: claude-skills/*.zip, test_workspace.zip, cv-assistant.zip")


def check_instructions():
    n = len(open(os.path.join(ROOT, "chatgpt", "GPT_INSTRUCTIONS.md"), encoding="utf-8").read())
    print(f"GPT_INSTRUCTIONS.md: {n} / {GPT_LIMIT} characters")
    if n > GPT_LIMIT:
        sys.exit("GPT instructions too long")


if __name__ == "__main__":
    check_instructions()
    copy_templates()
    build_knowledge()
    build_zips()
