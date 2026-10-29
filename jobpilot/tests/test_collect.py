"""Offline tests for jobpilot.collect (no network): normalizers, filters, dedup, closing, applied.csv.

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import csv
import datetime as dt
import os
import sys
import tempfile
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import collect as cj, store  # noqa: E402

TODAY = dt.datetime(2026, 10, 25, tzinfo=dt.timezone.utc)
FILTERS = {
    "title_include": ["data", "ai", "automation"],
    "title_exclude": ["director", "sales"],
    "description_exclude": ["us citizenship"],
    "locations": ["romania", "bucharest"],
    "allow_remote": True,
    "remote_regions": ["europe", "emea"],
    "max_age_days": 30,
}
FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def job(job_id, title, location="Bucharest, Romania", remote=None, published="2026-10-20T10:00:00Z", **kw):
    return {"job_id": job_id, "title": title, "location": location, "remote": remote, "published_at": published,
            "url": kw.pop("url", f"https://jobs.example.com/{job_id}"), "apply_url": None, "description": "", **kw}


def test_normalizers():
    gh = {"jobs": [{"id": 1, "title": "Data Engineer", "company_name": "Acme", "location": {"name": "Remote - EMEA"},
                    "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1", "first_published": "2026-10-20T11:31:50-04:00",
                    "content": "&lt;p&gt;Build &amp;amp; run &lt;b&gt;pipelines&lt;/b&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;",
                    "departments": [{"name": "Data"}]}]}
    lever = [{"id": "abc", "text": "AI Engineer", "categories": {"location": "Bucharest", "team": "AI", "commitment": "Full-time"},
              "workplaceType": "hybrid", "createdAt": 1792500000000, "descriptionPlain": "Intro",
              "lists": [{"text": "Requirements", "content": "<li>Python</li>"}], "additionalPlain": "Benefits",
              "hostedUrl": "https://jobs.lever.co/x/abc", "applyUrl": "https://jobs.lever.co/x/abc/apply"}]
    ashby = {"jobs": [{"id": "u1", "title": "Automation Analyst", "location": "Remote", "isRemote": True, "isListed": True,
                       "workplaceType": "Remote", "address": {"postalAddress": {"addressCountry": "Lithuania"}},
                       "secondaryLocations": [{"location": "Vilnius"}], "publishedAt": "2026-10-01T08:30:00.713+00:00",
                       "jobUrl": "https://jobs.ashbyhq.com/x/u1", "applyUrl": "https://jobs.ashbyhq.com/x/u1/application",
                       "descriptionPlain": "Plain text"},
                      {"id": "u2", "title": "Hidden", "isListed": False}]}
    orig = cj.http_json
    try:
        cj.http_json = lambda url, **k: gh
        g = cj.fetch_greenhouse("acme")[0]
        check("gh description", g["description"], "Build & run pipelines\n\n- SQL")
        check("gh published utc", g["published_at"], "2026-10-20T15:31:50Z")
        check("gh remote", g["remote"], 1)
        check("gh company", g["company"], "Acme")

        cj.http_json = lambda url, **k: lever
        lv = cj.fetch_lever("x")[0]
        check("lever remote hybrid", lv["remote"], 0)
        check("lever description has list", "Requirements\n- Python" in lv["description"], True)
        check("lever published", lv["published_at"][:10], "2026-10-20")

        calls = []

        def eu_fallback(url, **k):
            calls.append(url)
            if "api.lever.co" in url:
                raise urllib.error.HTTPError(url, 404, "nf", None, None)
            return lever
        cj.http_json = eu_fallback
        check("lever eu fallback", len(cj.fetch_lever("x")), 1)
        check("lever eu url", "api.eu.lever.co" in calls[-1], True)

        cj.http_json = lambda url, **k: ashby
        ab = cj.fetch_ashby("x")
        check("ashby unlisted skipped", len(ab), 1)
        check("ashby remote location keeps no HQ country", ab[0]["location"], "Remote / Vilnius")
    finally:
        cj.http_json = orig


def test_filters():
    f = cj.Filters(FILTERS)
    cases = [
        ("pass bucharest", job("1", "Data Engineer"), None),
        ("pass diacritics", job("2", "AI Developer", "București, România"), None),
        ("ai is a whole word", job("3", "Maintenance Technician"), "title: no include term"),
        ("exclude", job("4", "Director of Data"), "title: excluded 'director'"),
        ("remote plain", job("5", "Data Analyst", "Remote", remote=1), None),
        ("remote emea", job("6", "Data Analyst", "Remote - EMEA"), None),
        ("region as location", job("6b", "Data Analyst", "EMEA / United States"), None),
        ("remote us", job("6c", "Data Analyst", "Remote - US", remote=1), "location: Remote - US"),
        ("remote other country", job("7", "Data Analyst", "Remote, France", remote=1), "location: Remote, France"),
        ("onsite other city", job("8", "Data Analyst", "Berlin"), "location: Berlin"),
        ("too old", job("9", "Data Analyst", published="2026-08-01T00:00:00Z"), "age: published 2026-08-01"),
        ("no date kept", job("10", "Data Analyst", published=None), None),
        ("description exclude", job("11", "Data Analyst", description="Requires US citizenship."),
         "description: excluded 'us citizenship'"),
    ]
    for name, j, want in cases:
        check(f"filter {name}", f.check(j, TODAY), want)
    check("no location filters → any location", cj.Filters({}).check(job("12", "x", "Tokyo"), TODAY), None)
    check("remote disallowed", cj.Filters({**FILTERS, "allow_remote": False}).check(job("13", "Data", "Remote", remote=1), TODAY),
          "location: Remote")
    w = cj.Filters({**FILTERS, "workplaces": ["remote", "hybrid"]})
    check("workplace hybrid kept", w.check(job("14", "Data", workplace="hybrid"), TODAY), None)
    check("workplace on-site dropped", w.check(job("15", "Data", workplace="On-site"), TODAY), "workplace: onsite")
    check("workplace remote flag", w.check(job("16", "Data", "Remote", remote=1), TODAY), None)
    check("workplace unknown kept", w.check(job("17", "Data"), TODAY), None)
    check("remote only drops hybrid", cj.Filters({**FILTERS, "workplaces": ["remote"]}).check(
        job("18", "Data", workplace="hybrid"), TODAY), "workplace: hybrid")


def test_collect_dedup_close_applied():
    tmp = tempfile.mkdtemp()
    applied = os.path.join(tmp, "applied.csv")
    with open(applied, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "application", "status", "job_url", "form_url"])
        w.writerow(["2026-10-23", "x", "submitted", "", "https://jobs.example.com/applied-1/application"])
    config = {"boards": {"greenhouse": [{"board": "acme", "company": "Acme"}], "ashby": ["acme-ashby"]},
              "filters": FILTERS}
    feeds = {
        "greenhouse": [job("1", "Data Engineer"), job("2", "Sales Manager"), job("applied-1", "AI Developer")],
        "ashby": [job("9", "Data Engineer", company="Acme")],  # same job posted on a second board
    }
    fetchers = {s: (lambda b, s=s: feeds[s]) for s in feeds}
    db = store.connect(":memory:")
    s1 = cj.collect(db, config, applied, fetchers=fetchers, today=TODAY)
    status = {r["uid"]: r["status"] for r in db.execute("SELECT uid, status FROM jobs")}
    # boards are stored in sorted order (ashby before greenhouse): the first stored copy is the original
    check("run1 original", status["ashby:acme-ashby:9"], "new")
    check("run1 cross-board duplicate", status["greenhouse:acme:1"], "duplicate")
    check("run1 filtered", status["greenhouse:acme:2"], "filtered_out")
    check("run1 applied", status["greenhouse:acme:applied-1"], "applied")
    check("run1 new_matches", len(s1["new_matches"]), 1)

    s2 = cj.collect(db, config, applied, fetchers=fetchers, today=TODAY)
    check("run2 nothing new", (s2["new_matches"], sum(b["inserted"] for b in s2["boards"])), ([], 0))
    check("run2 row count", db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 4)

    # the original disappears from ashby → closed; the greenhouse copy is filtered again and becomes new
    feeds["ashby"] = []
    s3 = cj.collect(db, config, applied, fetchers=fetchers, today=TODAY)
    row = db.execute("SELECT closed_at FROM jobs WHERE uid = 'ashby:acme-ashby:9'").fetchone()
    check("run3 closed", row["closed_at"] is not None, True)
    check("run3 copy promoted", db.execute("SELECT status FROM jobs WHERE uid = 'greenhouse:acme:1'").fetchone()[0], "new")
    check("run3 new_matches", len(s3["new_matches"]), 1)

    # repost of an applied job with a new id → duplicate, never new
    feeds["greenhouse"].append(job("applied-2", "AI Developer"))
    cj.collect(db, config, applied, fetchers=fetchers, today=TODAY)
    check("repost of applied", db.execute("SELECT status FROM jobs WHERE uid = 'greenhouse:acme:applied-2'").fetchone()[0],
          "duplicate")

    # a failing board keeps its jobs open and does not stop the run
    def boom(board):
        raise urllib.error.URLError("down")
    s5 = cj.collect(db, config, applied, fetchers={"greenhouse": boom, "ashby": fetchers["ashby"]}, today=TODAY)
    check("error reported", len(s5["errors"]), 1)
    check("failed board not closed",
          db.execute("SELECT closed_at FROM jobs WHERE uid = 'greenhouse:acme:2'").fetchone()[0], None)

    # a manual status is never overwritten by the filters
    db.execute("UPDATE jobs SET status = 'skipped' WHERE uid = 'greenhouse:acme:1'")
    cj.collect(db, config, applied, fetchers=fetchers, today=TODAY)
    check("manual status kept", db.execute("SELECT status FROM jobs WHERE uid = 'greenhouse:acme:1'").fetchone()[0], "skipped")


def test_canonical_url():
    check("canonical apply", cj.canonical_url("https://Jobs.AshbyHQ.com/acmeco/A1/application?x=1"), "jobs.ashbyhq.com/acmeco/a1")
    check("canonical slash", cj.canonical_url("https://jobs.lever.co/x/abc/apply/"), "jobs.lever.co/x/abc")


if __name__ == "__main__":
    for t in (test_normalizers, test_filters, test_collect_dedup_close_applied, test_canonical_url):
        t()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all collect_jobs tests passed")
