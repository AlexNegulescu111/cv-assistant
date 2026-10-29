"""Check the answers pool against form labels - run after every pattern change in ANSWERS_POOL.json.

    python check_answers.py [--workspace DIR] [--tests answers_tests.json]
        → every case in the tests file (default: answers_tests.json next to the answers pool) answered as expected;
          exit 1 when one fails
    python check_answers.py --label "What is your notice period?" [--type select --options "1 month" "2 months"]
        → which pool entry answers this label, and with what

answers_tests.json (the user's own labels; one case per label that was ever answered wrongly or newly added):
    {"cases": [{"label": "Notice period*", "type": "text", "required": true, "expect": "1 month"},
               {"label": "Gender", "type": "select", "options": ["Male", "Female", "Prefer not to say"],
                "expect": "Prefer not to say"},
               {"label": "Passport number", "type": "text", "expect": {"pending": "do_not_answer"}},
               {"form": "acme", "label": "Salary (EUR)", "type": "number", "expect": 4000}]}
    Cases with the same "form" are matched together (variants with when_form_has look at the other labels).
"""
import argparse
import json
import os
import sys

try:
    from jobpilot import answers
    from jobpilot.workspace import Workspace
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    try:
        from jobpilot import answers
        from jobpilot.workspace import Workspace
    except ImportError:
        sys.exit("jobpilot is not installed: run  python -m pip install <path to the cv-assistant folder>")

OPTION_OBJECTS = ("radio", "buttons", "checkbox")


def field(n, case):
    f = {"field_id": f"f{n:02d}", "label": case["label"], "type": case.get("type", "text"),
         "required": bool(case.get("required"))}
    if case.get("hint"):
        f["hint"] = case["hint"]
    if case.get("options"):
        opts = case["options"]
        f["options"] = [{"label": o, "value": o, "selector": ""} for o in opts] if f["type"] in OPTION_OBJECTS else opts
    return f


def run_cases(pool, cases):
    forms = {}
    for case in cases:
        forms.setdefault(case.get("form", ""), []).append(case)
    failures = 0
    for name, group in forms.items():
        fields = [field(n, c) for n, c in enumerate(group)]
        got_answers, pending, _ = answers.match_form(fields, pool)
        reasons = {p["field_id"]: p["reason"] for p in pending}
        for f, case in zip(fields, group):
            fid = f["field_id"]
            got = {"pending": reasons[fid]} if fid in reasons else got_answers[fid]
            if got != case["expect"]:
                failures += 1
                print(f"FAIL [{name or '-'}] {case['label'][:70]!r}\n     expected {case['expect']!r}\n     got      {got!r}")
    print(f"{len(cases) - failures}/{len(cases)} passed")
    return failures


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace")
    ap.add_argument("--pool", help="default: the workspace answers pool")
    ap.add_argument("--tests", help="default: answers_tests.json next to the answers pool")
    ap.add_argument("--label")
    ap.add_argument("--type", default="text")
    ap.add_argument("--options", nargs="*")
    ap.add_argument("--required", action="store_true")
    a = ap.parse_args(argv)
    pool_path = a.pool or Workspace(a.workspace).path("answers_pool")
    pool = answers.load_pool(pool_path)
    if a.label:
        f = field(0, {"label": a.label, "type": a.type, "options": a.options, "required": a.required})
        value, entry, reason = answers.answer_field(f, [answers.plain(a.label)], pool)
        print(f"ENTRY   {entry or '-'}\nVALUE   {value!r}" + (f"\nPENDING {reason}" if reason else ""))
        return 0
    tests = a.tests or os.path.join(os.path.dirname(pool_path), "answers_tests.json")
    if not os.path.exists(tests):
        print(f"NO TESTS {tests} - create it with the first case (see --help)")
        return 0
    with open(tests, encoding="utf-8") as f:
        cases = json.load(f).get("cases", [])
    return 1 if run_cases(pool, cases) else 0


if __name__ == "__main__":
    sys.exit(main())
