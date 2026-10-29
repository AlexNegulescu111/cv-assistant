"""The daily schedule's entry point: one auto-apply run for a workspace, through the dashboard (see __main__.run_task).

    python -I run_task.py --workspace "<workspace folder>" [--dry-run]
Output is appended to <db folder>/dashboard_scheduled.log (pythonw has no console).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard.__main__ import main  # noqa: E402
from jobpilot.workspace import Workspace  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    root = args[args.index("--workspace") + 1] if "--workspace" in args else None
    log = os.path.join(os.path.dirname(Workspace(root).path("db")), "dashboard_scheduled.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as out:
        sys.stdout = sys.stderr = out
        print(f"--- {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}")
        sys.exit(main(args + ["--task", "auto_apply"]))
