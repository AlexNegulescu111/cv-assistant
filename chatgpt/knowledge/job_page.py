"""Job page / application form: extract the description and the form, fill the form in a browser.

The code lives in the jobpilot package (cv-assistant/jobpilot/page.py), shared with the collector and the scorer.
This file only finds it: an installed jobpilot (`pip install <cv-assistant folder>`), else the package next to this
skill when it runs from the cv-assistant folder itself. The workspace is the current folder (or $JOBPILOT_WORKSPACE).

    python job_page.py extract "<url>" --out output/applications/<company>_<role>/
    python job_page.py fill --form <dir>/form.json --answers <dir>/answers.json [--cv <pdf>] [--check]
"""
import os
import sys

try:
    from jobpilot import page
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    try:
        from jobpilot import page
    except ImportError:
        sys.exit("jobpilot is not installed: run  python -m pip install <path to the cv-assistant folder>  "
                 "(it also needs: pip install playwright && python -m playwright install chromium)")

if __name__ == "__main__":
    sys.exit(page.main(sys.argv[1:]))
