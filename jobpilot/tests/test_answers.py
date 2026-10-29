"""Offline tests for jobpilot.answers with a tiny fictional pool: matching, re-match keeps written answers, --record.

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
(A workspace's own pool is tested against real labels by its own test file, e.g. test_match_answers.py.)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import answers  # noqa: E402

FAILS = []
POOL = {
    "contact": {"email": "maria@example.com"},
    "screening": {}, "voluntary_disclosures": {}, "do_not_answer": [], "consents": [],
    "question_bank": [{"id": "email", "patterns": ["e-?mail"], "answer": "{contact.email}"}],
    "free_text": {"patterns": ["why .* interested"]},
}
FORM = {"fields": [
    {"field_id": "f1_email", "label": "Email", "type": "text", "required": True},
    {"field_id": "f2_why", "label": "Why are you interested in us?", "type": "textarea", "required": True},
]}


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def run(argv):
    out = sys.stdout
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    try:
        return answers.main(argv)
    finally:
        sys.stdout.close()
        sys.stdout = out


def test_rematch_and_record():
    d = tempfile.mkdtemp()
    pool, form = os.path.join(d, "pool.json"), os.path.join(d, "form.json")
    json.dump(POOL, open(pool, "w", encoding="utf-8"))
    json.dump(FORM, open(form, "w", encoding="utf-8"))
    read = lambda name: json.load(open(os.path.join(d, name), encoding="utf-8"))  # noqa: E731
    run([form, "--pool", pool])
    check("pool answer", read("answers.json")["answers"]["f1_email"], "maria@example.com")
    check("free text pending", [p["reason"] for p in read("pending.json")["pending"]], ["needs_writing"])
    a = read("answers.json")
    a["answers"]["f2_why"] = "Because of the product."
    json.dump(a, open(os.path.join(d, "answers.json"), "w", encoding="utf-8"))
    run([form, "--pool", pool])
    check("written answer kept", read("answers.json")["answers"]["f2_why"], "Because of the product.")
    check("kept field no longer pending", read("pending.json")["pending"], [])
    run([form, "--pool", pool, "--record"])
    check("record leaves answers.json", read("answers.json")["answers"]["f2_why"], "Because of the product.")
    run([form, "--pool", pool, "--fresh"])
    check("--fresh drops it", read("answers.json")["answers"]["f2_why"], None)


if __name__ == "__main__":
    test_rematch_and_record()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all answers tests passed")
