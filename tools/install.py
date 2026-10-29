"""Install the CV Assistant skills for Codex or Claude Code.

Usage:
    python tools/install.py --target codex  [--project DIR] [--force]
    python tools/install.py --target claude [--project DIR] [--force]

Without --project the skills are installed for the user (all folders):
    codex  → ~/.agents/skills/<skill>
    claude → ~/.claude/skills/<skill>
With --project DIR they go to DIR/.agents/skills or DIR/.claude/skills.
Existing skills with the same name are left alone unless --force is given.
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
sys.path.insert(0, ROOT)
from jobpilot.init import install_skills  # noqa: E402  (the copy `jobpilot init` makes too)
SUBDIR = {"codex": os.path.join(".agents", "skills"), "claude": os.path.join(".claude", "skills")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, choices=sorted(SUBDIR))
    ap.add_argument("--project")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    base = os.path.abspath(a.project) if a.project else os.path.expanduser("~")
    dest_root = os.path.join(base, SUBDIR[a.target])
    installed, skipped = install_skills(dest_root, force=a.force, source=SKILLS)
    for name in installed:
        print("installed", os.path.join(dest_root, name))
    if skipped:
        print("SKIPPED (already exist, rerun with --force to replace):", ", ".join(skipped))
    try:
        import docx  # noqa: F401
    except ImportError:
        print("NOTE install python-docx to generate CVs:  pip install python-docx pypdf")
    if skipped and not a.force:
        sys.exit(1)


if __name__ == "__main__":
    main()
