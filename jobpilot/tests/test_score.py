"""Offline tests for jobpilot.score (fake Jev client, in-memory db): extraction, profile, combining, storage, retries.

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import io
import json
import os
import sys
import tempfile
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import score as sj  # noqa: E402

FAILS = []
CFG = {"model": "jev-latest", "languages": ["English", "Romanian"], "max_description_chars": 9000, "max_must_have": 12, "max_nice_to_have": 6, "workers": 2,
       "weights": {"must_have_coverage": 0.35, "nice_to_have_coverage": 0.05, "skills_fit": 0.30,
                   "seniority_fit": 0.15, "domain_fit": 0.15},
       "hard_requirement_weight": 2.0,
       "thresholds": {"hard": 0.6, "met": 0.7, "not_met": 0.3, "language_knockout": 0.7, "low_confidence": 0.45,
                      "apply": 70, "review": 50}}
DESCRIPTION = """ABOUT YOUR TEAM

You'll work with Databricks and Slack every day on support data.

WHAT WE'RE LOOKING FOR:

 - 3+ years of experience with Python and SQL in production.
 - Strong communication with non-technical stakeholders.
 - Exposure to payments is a plus, not a prerequisite for this role.
 - We are an equal opportunity employer and encourage you to apply.

Nice to have

 - Experience with dbt models and tests.

Don't meet every single requirement?

Studies show people apply only when they tick every box, please apply anyway.
"""


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def test_extract():
    r = sj.extract_requirements(DESCRIPTION)
    check("must items", r["must"], ["3+ years of experience with Python and SQL in production.",
                                    "Strong communication with non-technical stakeholders."])
    check("nice items", r["nice"], ["Exposure to payments is a plus, not a prerequisite for this role.",
                                    "Experience with dbt models and tests."])
    check("no section → nothing", sj.extract_requirements("Just a paragraph about the company."), {"must": [], "nice": []})
    lines = "Requirements\n" + "\n".join(f"- requirement number {i} with enough text" for i in range(20))
    check("cap", len(sj.extract_requirements(lines, max_must=5)["must"]), 5)
    plain = "Who you are\nYou have shipped Python services to production.\nYou like small teams and fast feedback."
    check("lines without bullets", len(sj.extract_requirements(plain)["must"]), 2)

    # formats seen on company career pages (peviitor): emoji / "we'd love to see", "Required Skills:"
    emoji_ad = ("🔍 WHAT WILL YOU BE DOING?\n\n - Leading the design of core backend services\n\n"
              "👀 WHAT WE’D LOVE TO SEE:\n\n - In-depth understanding of the development process\n\n"
              " - Experience in Python is advantageous\n\n✅ A TYPICAL INTERVIEW PROCESS\n\n - An initial intro call with us")
    check("we'd love to see", sj.extract_requirements(emoji_ad)["must"],
          ["In-depth understanding of the development process", "Experience in Python is advantageous"])
    colon_ad = ("Responsibilities:\n\n- Designing data warehouse solutions.\n\nWhat you'll bring\n\nRequired Skills:\n\n"
          "- Intermediate-level SQL querying.\n\nDesirable Experience:\n\n- Experience with dbt models.\n\nOur Behaviours\n\n- We are open")
    r = sj.extract_requirements(colon_ad)
    check("Required Skills / Desirable", (r["must"], r["nice"]), (["Intermediate-level SQL querying."], ["Experience with dbt models."]))
    flat = ("At Acme Research you build BI. Here’s a breakdown of what you’ll do: Design dashboards in BI tools. "
            "These are the essentials you’ll need to get an interview: Very good skills in BI tools, including DAX. "
            "Strong SQL skills. Good Python skills for data work (pandas, NumPy). It would be great if you also had: "
            "Experience with Azure OpenAI. What we offer: Private medical insurance for you.")
    r = sj.extract_requirements(flat)
    check("flat paragraph: inline cues", (r["must"], r["nice"]),
          (["Very good skills in BI tools, including DAX.", "Strong SQL skills.", "Good Python skills for data work (pandas, NumPy)."],
           ["Experience with Azure OpenAI."]))
    yougov = ("These are the essentials you’ll need to get an interview:\n\n- Strong SQL skills.\n\n"
              "We offer a hybrid working model in an exciting work environment, 3 days per week working from home:\n\n"
              "- Initial and ongoing trainings to support your development.\n\n- Food vouchers & private medical subscription")
    check("a long 'We offer ...:' lead-in ends the requirements", sj.extract_requirements(yougov)["must"], ["Strong SQL skills."])
    epam = ("We need a data engineer. Responsibilities Manage the full lifecycle of data pipelines Requirements "
            "Bachelor's or Master's degree in Computer Science or another technical discipline Minimum of 5 years of prior "
            "experience in a related role Familiarity with SDLC tools and frameworks We offer Learning opportunities")
    check("flat run-on list: bare section word + item starts", sj.extract_requirements(epam)["must"],
          ["Bachelor's or Master's degree in Computer Science or another technical discipline",
           "Minimum of 5 years of prior experience in a related role", "Familiarity with SDLC tools and frameworks"])


def test_profile():
    tmp = tempfile.mkdtemp()
    pool, draft = os.path.join(tmp, "pool.md"), os.path.join(tmp, "draft.md")
    with open(pool, "w", encoding="utf-8") as f:
        f.write("# Pool\n\n## Core skill pool\n- Python\n\nDo not claim yet without additional evidence:\n- Kubernetes\n\n"
                "## Information to collect before finalizing CVs\n- LinkedIn: https://example.com/secret\n")
    with open(draft, "w", encoding="utf-8") as f:
        f.write("## Work Experience\n\n### Data Engineer | Acme | Oct. 2025 - Present\n- tailored bullet\n\n"
                "## Education\n- B.Sc. CS\n\n## Languages\nRomanian (native) | English (advanced)\n")
    p = sj.build_profile(pool, draft)
    check("pool kept", "- Python" in p and "Kubernetes" in p, True)
    check("information to collect dropped", "secret" in p, False)
    check("roles", "- Data Engineer | Acme | Oct. 2025 - Present" in p, True)
    check("tailored bullets dropped", "tailored bullet" in p, False)
    check("languages", "English (advanced)" in p, True)


def fake_answers(reqs, met=0.9, hard=0.9, score_frac=1.0, confidence=0.9, lang=0.05):
    a = {}
    for kind in ("must", "nice"):
        for i in range(len(reqs[kind])):
            a[f"{kind}_{i}_met"] = {"type": "noul", "noul": met[i] if isinstance(met, list) else met}
            if kind == "must":
                a[f"must_{i}_hard"] = {"type": "noul", "noul": hard[i] if isinstance(hard, list) else hard}
    a["language_block"] = {"type": "noul", "noul": lang}
    for qid, (_, levels) in sj.rubrics(CFG).items():
        a[qid] = {"type": "score", "score": score_frac * (len(levels) - 1), "confidence": confidence, "probabilities": {}}
    a["role_family"] = {"type": "choice", "choice": "Data", "confidence": 0.9}
    return a


def test_questions_and_combine():
    reqs = {"must": ["Python", "Communication"], "nice": ["dbt"]}
    q = sj.build_questions(reqs, CFG)
    check("question count", len(q), 2 * 2 + 1 + 1 + len(sj.rubrics(CFG)))  # no role_families → no Choice
    check("language question", q["language_block"]["instructions"],
          "The job requires fluency in a language other than English or Romanian.")
    q2 = sj.build_questions(reqs, {"role_families": {"Data": "data work", "Other": "anything else"}},
                            "## Languages\nRomanian (native) | English (advanced) | Russian (intermediate)\n")
    check("languages from profile", q2["language_block"]["instructions"],
          "The job requires fluency in a language other than Romanian, English or Russian.")
    check("role family with config", q2["role_family"]["criteria"], {"Data": "data work", "Other": "anything else"})
    check("domain from config", sj.rubrics({"target_domain": "data work"})["domain_fit"][0], "How central data work is to this job")
    check("score levels ≤ 10", all(len(v["criteria"]) <= 10 for v in q.values() if v["type"] == "score"), True)

    r = sj.combine(reqs, fake_answers(reqs), CFG)
    check("apply verdict", (r["verdict"], r["knockouts"], r["flags"]), ("apply", [], []))
    check("apply match", r["match"], 96.0)  # 0.35*0.9 + 0.05*0.9 + 0.6*1.0

    r = sj.combine(reqs, fake_answers(reqs, met=[0.1, 0.9], hard=[0.9, 0.1]), CFG)
    check("hard unmet → knockout", (r["verdict"], r["knockouts"]), ("knockout", ["Python"]))

    r = sj.combine(reqs, fake_answers(reqs, met=[0.9, 0.1], hard=[0.9, 0.1]), CFG)
    check("soft unmet → no knockout", r["knockouts"], [])
    check("hard weighs double", r["details"]["parts"]["must_have_coverage"], round((2 * 0.9 + 0.1) / 3, 3))

    r = sj.combine(reqs, fake_answers(reqs, met=[0.5, 0.9], hard=[0.9, 0.9]), CFG)
    check("unsure hard → review", (r["verdict"], r["flags"][0][:7]), ("review", "unsure:"))

    r = sj.combine(reqs, fake_answers(reqs, lang=0.9), CFG)
    check("language knockout", r["verdict"], "knockout")

    r = sj.combine(reqs, fake_answers(reqs, confidence=0.3), CFG)
    check("low confidence → review", r["verdict"], "review")

    none = {"must": [], "nice": []}
    r = sj.combine(none, fake_answers(none, score_frac=0.5), CFG)
    check("no requirements: rubric-only match", r["match"], 50.0)
    check("no requirements flagged", r["flags"], ["no requirements section found: match from rubrics only"])

    r = sj.combine(reqs, fake_answers(reqs, met=0.2, hard=0.1, score_frac=0.0), CFG)
    check("low verdict", r["verdict"], "low")

    yreqs = {"must": ["5+ years of data engineering", "Python"], "nice": []}
    for text in ("3-5 years of analytics", "Minimum 4–6+ years", "+8 years of embedded", "6/8+ years", "at least 2 ani"):
        check(f"years pattern: {text}", bool(sj.YEARS.search(text)), True)
    check("not a years requirement", bool(sj.YEARS.search("Python 3 and SQL")), False)
    unmet_years = fake_answers(yreqs, met=[0.1, 0.9], hard=[0.9, 0.9])
    check("years unmet → knockout by default", sj.combine(yreqs, unmet_years, CFG)["verdict"], "knockout")
    soft = dict(CFG, experience_requirements={"knockout": False, "weight": 0.5},
                thresholds=dict(CFG["thresholds"], flags_block_apply=False))
    r = sj.combine(yreqs, unmet_years, soft)
    check("years unmet, knockout off → flag, still apply", (r["verdict"], r["knockouts"], r["flags"][0][:11]),
          ("apply", [], "experience:"))
    check("years weighs experience weight", r["details"]["parts"]["must_have_coverage"], round((0.5 * 0.1 + 2 * 0.9) / 2.5, 3))
    check("combine key changes with experience settings", sj.combine_key(soft) != sj.combine_key(CFG), True)


class FakeClient:
    def __init__(self, fail_title=None):
        self.calls, self.fail_title = 0, fail_title

    def system_one(self, state, questions):
        self.calls += 1
        if state["job"]["title"] == self.fail_title:
            raise RuntimeError("Jev HTTP 500: boom")
        reqs = {"must": [k for k in questions if k.endswith("_hard")], "nice": [k for k in questions if k.startswith("nice_")]}
        high = "Data" in state["job"]["title"]
        return {"model": "jev-test", "usage": {"input_tokens": 1000},
                "answers": fake_answers(reqs, met=0.9 if high else 0.2, hard=0.9, score_frac=1.0 if high else 0.0)}


def test_storage_and_rank():
    db = sj.connect(":memory:")
    for i, title in enumerate(["Data Engineer", "Haskell Developer", "Broken Job"], 1):
        db.execute("INSERT INTO jobs (uid, source, board, job_id, company, title, location, description, fingerprint, "
                   "status, first_seen, last_seen) VALUES (?, 'ashby', 'x', ?, 'Acme', ?, 'Remote', ?, ?, 'new', 'now', 'now')",
                   (f"ashby:x:{i}", str(i), title, DESCRIPTION, f"fp{i}"))
    profile = "profile text"
    jobs = sj.pending_jobs(db, profile, CFG)
    check("pending all", len(jobs), 3)
    client = FakeClient(fail_title="Broken Job")
    res = sj.score_jobs(db, CFG, client, profile, jobs)
    check("one error captured", sum("error" in r for r in res), 1)
    check("two stored", db.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 2)
    check("only the failed job pending", [j["title"] for j, _ in sj.pending_jobs(db, profile, CFG)], ["Broken Job"])
    check("profile change → rescore", len(sj.pending_jobs(db, profile + " new skill", CFG)), 3)
    check("request config change → rescore", len(sj.pending_jobs(db, profile, {**CFG, "max_must_have": 3})), 3)
    strict = {**CFG, "thresholds": {**CFG["thresholds"], "apply": 99}}
    check("weights/thresholds change → no API call", len(sj.pending_jobs(db, profile, strict)), 1)
    check("recombined locally", sj.recombine(db, strict), 2)
    check("verdict follows new threshold",
          db.execute("SELECT verdict FROM scores WHERE job_id = 1 ORDER BY id DESC LIMIT 1").fetchone()[0], "review")
    check("recombine idempotent", sj.recombine(db, strict), 0)
    rows = db.execute(sj.RANK_SQL.format(extra="")).fetchall()
    check("rank order", [(r["title"], r["verdict"]) for r in rows], [("Data Engineer", "review"), ("Haskell Developer", "knockout")])
    check("rank line", "✗" in sj.rank_line(rows[1]), True)
    check("back to the old thresholds", (sj.recombine(db, CFG), db.execute(sj.RANK_SQL.format(extra="")).fetchone()["verdict"]),
          (2, "apply"))


def test_client_retries():
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append(dict(req.header_items()))
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 422, "x", {}, io.BytesIO(b'{"error":"state exceeds context, use long context"}'))
        return Resp(json.dumps({"answers": {}, "model": "jev"}).encode())

    orig = sj.urllib.request.urlopen
    sj.urllib.request.urlopen = fake_urlopen
    try:
        out = sj.JevClient("k", "jev-latest").system_one({"a": 1}, {"q": {"type": "noul", "instructions": "x"}})
    finally:
        sj.urllib.request.urlopen = orig
    check("long-context retry answered", out["model"], "jev")
    check("second call has header", any(k.lower() == "typesafe-danger-zone-long-context" for k in calls[1]), True)
    check("auth header", calls[0].get("Authorization"), "Bearer k")


if __name__ == "__main__":
    for t in (test_extract, test_profile, test_questions_and_combine, test_storage_and_rank, test_client_retries):
        t()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all score_jobs tests passed")
