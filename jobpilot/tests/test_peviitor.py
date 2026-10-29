"""Offline tests for jobpilot.peviitor and the partial-source path of jobpilot.collect (no network).

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import collect, peviitor, store  # noqa: E402

FAILS = []
LONG = "We build data pipelines. " * 40


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def test_route():
    boards = {"ashby": {"acmeco"}, "greenhouse": {"gitlab"}}
    cases = [
        ("https://jobs.ashbyhq.com/acmeco/123", ("covered", "ashby:acmeco")),
        ("https://jobs.ashbyhq.com/Acmeco/123/application", ("covered", "ashby:Acmeco")),
        ("https://jobs.ashbyhq.com/n8n/9", ("suggest", ("ashby", "n8n"))),
        ("https://jobs.eu.lever.co/acme/5", ("suggest", ("lever", "acme"))),
        ("https://job-boards.greenhouse.io/gitlab/jobs/1", ("covered", "greenhouse:gitlab")),
        ("https://boards.greenhouse.io/embed/job_board?for=datadog&b=x", ("suggest", ("greenhouse", "datadog"))),
        ("https://www.bestjobs.eu/loc-de-munca/x", ("aggregator", "bestjobs.eu")),
        ("https://ro.linkedin.com/jobs/view/1", ("blocked", "ro.linkedin.com")),
        ("https://tremend.com/career/data-engineer/", ("keep", "company site")),
        ("https://acme.wd3.myworkdayjobs.com/x/job/1", ("keep", "Workday")),
    ]
    for url, want in cases:
        check(f"route {url}", peviitor.route(url, boards), want)


def test_fetch():
    pages = {1: [{"title": "Data &amp; AI Engineer", "company": "ACME SRL", "city": ["București"], "workmode": "remote",
                  "url": "https://acme.ro/jobs/1", "date": "2026-10-24T10:45:40.793Z"},
                 {"title": "Dup", "company": "ACME SRL", "url": ["https://acme.ro/jobs/1"]}],
             2: [{"title": "Analyst", "company": "B SRL", "city": "Cluj", "workmode": "hybrid", "url": "https://b.ro/2"}]}
    calls = []

    def fake(path, params, timeout=30):
        calls.append(params)
        return {"response": {"docs": pages.get(params["page"], [])}}
    orig = peviitor.http_json
    peviitor.http_json = fake
    try:
        jobs = peviitor.fetch_peviitor("data engineer", city="Bucuresti", rows=2, pages=3)
    finally:
        peviitor.http_json = orig
    check("dedup by url across a page", [j["url"] for j in jobs], ["https://acme.ro/jobs/1", "https://b.ro/2"])
    check("stops on a short page", len(calls), 2)
    check("params", calls[0], {"q": "data engineer", "page": 1, "rows": 2, "city": "Bucuresti"})
    check("remote + location", (jobs[0]["remote"], jobs[0]["location"]), (1, "București (remote)"))
    check("hybrid", (jobs[1]["remote"], jobs[1]["location"]), (0, "Cluj"))
    check("date", jobs[0]["published_at"], "2026-10-24T10:45:40Z")
    check("HTML entities decoded", jobs[0]["title"], "Data & AI Engineer")
    check("stable id", jobs[0]["job_id"], peviitor.fetch_peviitor.__globals__["hashlib"].sha1(b"https://acme.ro/jobs/1").hexdigest()[:16])


def test_describe():
    class Resp(io.BytesIO):
        headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    pages = {
        "ld": '<html><script type="application/ld+json">{"@type": "JobPosting", "description": "<p>Build <b>ETL</b></p>"}'
              '</script><main>ignored</main></html>',
        "main": f"<html><nav>menu</nav><main><h1>Data Engineer</h1><p>{LONG}</p></main><footer>x</footer></html>",
        "short": "<html><main>Loading...</main></html>",
        # JSON-LD with the ad flattened into one paragraph, the page with its headings: the page wins
        "flatld": ('<html><script type="application/ld+json">{"@type": "JobPosting", "description": "' + LONG.strip() + '"}'
                   '</script><main>' + "".join(f"<h3>Section {i}</h3><ul><li>{LONG[:60]}</li></ul>" for i in range(12))
                   + "</main></html>"),
    }
    orig = peviitor.urllib.request.urlopen
    peviitor.urllib.request.urlopen = lambda req, timeout=None: Resp(pages[req.full_url.rsplit("/", 1)[1]].encode())
    try:
        check("JSON-LD first", peviitor.describe("https://x/ld"), "Build ETL")
        got = peviitor.describe("https://x/main")
        check("main text, no nav", (got.startswith("Data Engineer"), "menu" in got), (True, False))
        check("JS-only page", peviitor.describe("https://x/short"), "")
        got = peviitor.describe("https://x/flatld")
        check("structured page preferred over flat JSON-LD", (got.startswith("Section 0"), got.count("\n") > 20), (True, True))
    finally:
        peviitor.urllib.request.urlopen = orig
    calls = []

    def workday(req, timeout=None):
        calls.append(req.full_url)
        return Resp(json.dumps({"jobPostingInfo": {"jobDescription": "<p>Role</p><ul>" +
                                "".join(f"<li>{LONG[:80]}</li>" for _ in range(8)) + "</ul>"}}).encode())
    peviitor.urllib.request.urlopen = workday
    try:
        got = peviitor.describe("https://lseg.wd3.myworkdayjobs.com/en-US/Careers/job/Bucharest-Romania/Senior-Data-Engineer_R1?src=x")
        check("Workday JSON endpoint", calls[0], "https://lseg.wd3.myworkdayjobs.com/wday/cxs/lseg/Careers/job/Bucharest-Romania/Senior-Data-Engineer_R1")
        check("Workday description structured", (got.startswith("Role"), got.count("- ") >= 8), (True, True))
    finally:
        peviitor.urllib.request.urlopen = orig


def test_collect_partial():
    d = tempfile.mkdtemp()
    cfg_path = os.path.join(d, "job_sources.json")
    config = {"boards": {"ashby": ["acmeco"], "peviitor": [{"board": "data engineer", "rows": 50}]},
              "filters": {"title_include": ["data"], "description_exclude": ["security clearance"]}}
    json.dump(config, open(cfg_path, "w", encoding="utf-8"))
    feed = [
        {"job_id": "p1", "title": "Data Engineer", "company": "ACME", "url": "https://acme.ro/jobs/1", "location": "Cluj"},
        {"job_id": "p2", "title": "Data Analyst", "company": "Acmeco", "url": "https://jobs.ashbyhq.com/acmeco/7"},
        {"job_id": "p3", "title": "Data Engineer", "company": "n8n", "url": "https://jobs.ashbyhq.com/n8n/9"},
        {"job_id": "p4", "title": "Data Clerk", "company": "X", "url": "https://www.ejobs.ro/user/locuri-de-munca/x/1"},
        {"job_id": "p5", "title": "Data Engineer (cleared)", "company": "DefenceCo", "url": "https://def.ro/jobs/5"},
        {"job_id": "p6", "title": "Data Engineer", "company": "ACME", "url": "https://acme.ro/jobs/1-iasi", "location": "Iasi"},
    ]
    fetchers = {"ashby": lambda b: [{"job_id": "a7", "title": "Data Analyst", "company": "Acmeco",
                                     "url": "https://jobs.ashbyhq.com/acmeco/7", "description": LONG}],
                "peviitor": lambda b, **o: [dict(j) for j in feed]}
    texts = {"https://acme.ro/jobs/1": LONG, "https://def.ro/jobs/5": LONG + " Requires security clearance."}
    orig = peviitor.describe
    peviitor.describe = lambda url: texts.get(url, "")
    try:
        db = store.connect(":memory:")
        s = collect.collect(db, config, None, fetchers=fetchers)
        rows = {r["uid"]: r for r in db.execute("SELECT * FROM jobs")}
        check("stored: company sites only + the ATS job", sorted(rows),
              ["ashby:acmeco:a7", "peviitor:data engineer:p1", "peviitor:data engineer:p5", "peviitor:data engineer:p6"])
        check("the same posting in another city is a duplicate", rows["peviitor:data engineer:p6"]["status"], "duplicate")
        pv = next(b for b in s["boards"] if b["board"].startswith("peviitor"))
        check("route counts", pv["routes"], {"aggregator": 1, "covered": 1, "keep": 3, "suggest": 1})
        check("described", (s["described"], len(rows["peviitor:data engineer:p1"]["description"]) > 300), (2, True))
        check("description filter after describe", rows["peviitor:data engineer:p5"]["status"], "filtered_out")
        check("company site is new", rows["peviitor:data engineer:p1"]["status"], "new")
        sug = [dict(r) for r in db.execute("SELECT source, board, jobs_seen, status FROM board_suggestions")]
        check("suggestion", sug, [{"source": "ashby", "board": "n8n", "jobs_seen": 1, "status": "pending"}])
        collect.collect(db, config, None, fetchers=fetchers)
        check("seen again", db.execute("SELECT jobs_seen FROM board_suggestions").fetchone()[0], 2)
        fetchers["peviitor"] = lambda b, **o: []
        collect.collect(db, config, None, fetchers=fetchers)
        check("partial source never closes", db.execute("SELECT closed_at FROM jobs WHERE uid = 'peviitor:data engineer:p1'")
              .fetchone()[0], None)
        with redirect_stdout(io.StringIO()):
            collect.suggest(db, cfg_path, add=["ashby:n8n"])
        saved = json.load(open(cfg_path, encoding="utf-8"))
        check("added to job_sources.json", saved["boards"]["ashby"], ["acmeco", {"board": "n8n", "company": "n8n"}])
        check("now covered", peviitor.route("https://jobs.ashbyhq.com/n8n/9", collect.configured_boards(saved))[0], "covered")
        check("status added", db.execute("SELECT status FROM board_suggestions").fetchone()[0], "added")
    finally:
        peviitor.describe = orig


TESTS = (test_route, test_fetch, test_describe, test_collect_partial)

if __name__ == "__main__":
    for t in TESTS:
        t()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all peviitor tests passed")
