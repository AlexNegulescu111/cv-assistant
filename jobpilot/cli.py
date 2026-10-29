"""jobpilot - one command for the job pipeline. Code lives in the package; data lives in a workspace folder.

    python -m jobpilot init     [--workspace DIR] [--open]   new workspace: configs, empty answers pool, skills, checklist (init.py)
    python -m jobpilot collect  run|list|show ...            public ATS job boards → jobs.sqlite (collect.py)
    python -m jobpilot score    score|rank|explain|requirements ...   Jev scoring vs the skills pool (score.py)
    python -m jobpilot answers  <form.json> ...              application form ← answers pool, no AI (answers.py)
    python -m jobpilot page     extract|fill ...             job page / form in a browser (page.py, needs Playwright)
    python -m jobpilot apply    plan|run|set-cv|resume ...   top-scored jobs → forms, under a daily cap (apply.py)
    python -m jobpilot sites    list|show|match|probe ...    site template library: how each ATS / career site applies (sites.py)
    python -m jobpilot status | mark | link | history        job statuses and their history (pipeline.py)

Every command takes --workspace DIR (default: $JOBPILOT_WORKSPACE, else the current folder).
"""
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "init":
        from .init import init
        return init(rest)
    if cmd in ("status", "mark", "link", "history"):
        from . import pipeline
        return pipeline.main([cmd] + rest)
    modules = {"collect": "collect", "score": "score", "answers": "answers", "page": "page", "apply": "apply",
               "sites": "sites"}
    if cmd not in modules:
        print(f"unknown command '{cmd}'\n{__doc__}", file=sys.stderr)
        return 2
    module = __import__(f"jobpilot.{modules[cmd]}", fromlist=["main"])
    return module.main(rest)
