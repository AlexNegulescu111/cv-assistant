"""Offline tests for jobpilot.sites: shipped ATS templates, matching, learning a career site, privacy of templates.

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import page, sites  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def form(url, fields=None, **kw):
    return {"job_url": url, "form_url": kw.pop("form_url", url + "/apply"), "description_found": True,
            "fields": fields if fields is not None else [
                {"field_id": "f01_email", "label": "Email *", "type": "text", "required": True, "selector": "#email"},
                {"field_id": "f02_cv", "label": "CV", "type": "file", "required": True, "selector": "#cv"}], **kw}


def test_shipped_templates():
    lib = sites.Library(None)
    ids = set(lib.templates)
    check("shipped ATS", {"ashby", "greenhouse", "lever", "workable", "workday", "phenom", "avature"} <= ids, True)
    check("every shipped template is an ATS", {t["kind"] for t in lib.templates.values()}, {"ats"})
    check("page ATS map covered", {n for n in page.ATS.values()} - {t["name"] for t in lib.templates.values()},
          {"eJobs", "BestJobs"})  # job boards, not ATS forms
    check("match by host", lib.match("https://jobs.ashbyhq.com/acmeco/123")["id"], "ashby")
    check("match subdomain", lib.match("https://acme.wd3.myworkdayjobs.com/x")["id"], "workday")
    check("match by page marker", lib.match("https://jobs-cee.pwc.com/ro/ro/job/1", "<script src=x.phenompeople.com>")["id"], "phenom")
    check("unknown site", lib.match("https://careers.example.ro/job/1"), None)
    check("submit pattern", bool(lib.patterns("ashby", "submit_text").match("Submit Application")), True)
    check("aggregator", (sites.is_aggregator("https://mediere.anofm.ro/x"), sites.is_aggregator("https://careers.bmwtechworks.ro/x")),
          (True, False))


def test_learn_site():
    d = tempfile.mkdtemp()
    lib = sites.Library(d)
    url = "https://careers.example.ro/jobs/12345-data-engineer"
    tid = lib.learn_extract(url, form(url), {"description_selector": ".job-body", "apply_text": "Aplică acum",
                                             "submit_text": "Trimite", "captcha": False, "login": "none", "ats": ""})
    check("new site template id", tid, "site:careers.example.ro")
    path = os.path.join(d, "site_careers.example.ro.json")
    check("written in the workspace", os.path.exists(path), True)
    t = json.load(open(path, encoding="utf-8"))
    check("learned keys", (t["kind"], t["apply_text"], t["submit_text"], t["description_selector"], t["captcha"], t["verified"]),
          ("site", "Aplică acum", "Trimite", ".job-body", False, False))
    check("fields: structure only", t["fields"], [{"label": "Email *", "type": "text", "required": True},
                                                  {"label": "CV", "type": "file", "required": True}])
    check("form url pattern", t["form_url_pattern"], "/jobs/<n>-data-engineer/apply")
    blob = json.dumps(t)
    check("no answers or selectors of values in a template", ("maria" in blob, "answers" in blob), (False, False))

    lib = sites.Library(d)
    check("matched next time", lib.match("https://careers.example.ro/jobs/999")["id"], tid)
    lib.learn_fill(tid, "submitted", submit_label="Trimite aplicația", confirmation="Mulțumim, am primit candidatura ta!",
                   captcha=False)
    t = sites.Library(d).get(tid)
    check("verified after a confirmed submit", (t["verified"], t["submit_text"]), (True, "Trimite aplicația"))
    rx = sites.Library(d).patterns(tid, "confirmation")
    check("confirmation phrase learned", bool(rx.search("... Mulțumim, am primit candidatura ta! ...")), True)
    check("stats", (t["stats"]["extracts"], t["stats"]["fills"], t["stats"]["submitted"]), (1, 1, 1))
    lib = sites.Library(d)
    lib.learn_fill(tid, "submitted", confirmation="Mulțumim, am primit candidatura ta!")
    check("phrase not duplicated", len(sites.Library(d).get(tid)["confirmation"]), 1)
    other = sites.Library(d).learn_extract("https://jobs.other.ro/1", form("https://jobs.other.ro/1"),
                                           {"description_selector": "body"})
    check("body selector never learned", "description_selector" in sites.Library(d).get(other), False)


def test_ats_host_and_embedded():
    d = tempfile.mkdtemp()
    lib = sites.Library(d)
    tid = lib.learn_extract("https://jobs.ashbyhq.com/acme/1", form("https://jobs.ashbyhq.com/acme/1"), {"ats": "Ashby"})
    check("ATS host → ATS template", tid, "ashby")
    check("shipped template not copied", os.listdir(d), ["_stats.json"])
    check("ATS stats in _stats.json", json.load(open(os.path.join(d, "_stats.json")))["ashby"]["extracts"], 1)
    tid = lib.learn_extract("https://jobs-cee.pwc.com/ro/ro/job/7", form("https://jobs-cee.pwc.com/ro/ro/job/7"),
                            {"ats": "Phenom", "apply_text": "Apply Now"})
    t = sites.Library(d).get(tid)
    check("company domain with an embedded ATS → its own site template", (tid, t["embedded_ats"]),
          ("site:jobs-cee.pwc.com", "Phenom"))


def test_workspace_override():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "ashby.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "ashby", "kind": "ats", "name": "Ashby", "match": {"hosts": ["ashbyhq.com"]},
                   "submit_text": ["Send it"]}, f)
    lib = sites.Library(d)
    check("workspace overrides a shipped template", bool(lib.patterns("ashby", "submit_text").match("Send it")), True)


def test_confirmation_line():
    text = "https://x/thanks\nHome\nThank you for applying to Acme!\nMore jobs"
    check("generic phrase", page.confirmation_line(text), "Thank you for applying to Acme!")
    import re
    check("site phrase", page.confirmation_line("Am primit dosarul tău", re.compile("am primit dosarul", re.I)),
          "Am primit dosarul tău")


def test_label_rules():
    cases = [("First name* Required", ("First name*", True)), ("Email *", ("Email *", True)),
             ("Nume (obligatoriu)", ("Nume", True)), ("Phone", ("Phone", False)),
             ("Required skills you have", ("Required skills you have", False))]
    for label, want in cases:
        check(f"required_from_label {label!r}", page.required_from_label(label), want)
    for label, want in (("Log in", True), ("Sign in with LinkedIn", True), ("Abonează-te", True),
                        ("Submit application", False), ("Trimite", False), ("Apply", False)):
        check(f"not a submit button {label!r}", bool(page.NOT_SUBMIT.search(label)), want)


TESTS = (test_label_rules, test_shipped_templates, test_learn_site, test_ats_host_and_embedded, test_workspace_override, test_confirmation_line)

if __name__ == "__main__":
    for t in TESTS:
        t()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all sites tests passed")
