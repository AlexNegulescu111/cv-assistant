"""Score collected jobs against MASTER_SKILLS_POOL.md with Jev (TypeSafe System One): must-have knockouts + weighted match.

Usage (every command takes --workspace, --db and --config; paths default to the workspace, see workspace.py):
    python -m jobpilot score score [--ids 12 34] [--limit N] [--force] [--dry-run]
        → scores open `new` jobs that have no score for the current profile/config/description, one Jev request
          per job, saves the result in the `scores` table and prints the ranking of this run.
          --dry-run: shows what would be sent (requirements, question count, state size); no API call, nothing saved.
    python -m jobpilot score rank [--verdict apply|review|low|knockout] [--limit N] [--json]
        → open `new` jobs with their latest score, best first.
    python -m jobpilot score explain <id>
        → requirement by requirement: hard?, met?, plus every rubric score and its confidence.
    python -m jobpilot score requirements <id>
        → the must-have / nice-to-have lines extracted from the description (no API call).

One request per job. State = {candidate: profile built from the workspace skills pool (up to the
`profile_stop_heading`, default "Information to collect") + employment history, education, certifications and
languages from scoring.json → reference_draft; job: title, company, location, description}. Questions:
    must_<i>_met / nice_<i>_met   Noul  the profile shows evidence for requirement i
    must_<i>_hard                 Noul  requirement i is a hard qualification (tool, years, degree, language, licence)
    language_block                Noul  the job requires a language the candidate does not speak
    skills_fit, seniority_fit, domain_fit   Score rubrics;   role_family   Choice (only with `role_families`)
Knockout = a hard must-have that is clearly not met, or the language block. The match (0-100) is a weighted sum of
normalized parts (weights in scoring.json); verdict apply / review / low / knockout.
Optional scoring.json keys: `experience_requirements` {"knockout": bool, "weight": w} - must-haves that ask for a
number of years (e.g. "5+ years of ...") count with weight w and, with "knockout": false, never knock out (an unmet
one becomes an "experience:" flag); `thresholds.flags_block_apply` (default true) - false lets a flagged job above
the apply threshold still be `apply`.

Candidate-specific scoring.json keys: `languages` (default: read from the profile's "Languages" section),
`target_domain` (the kind of work the candidate is after, e.g. "data, AI, automation or integration"),
`role_families` ({label: description}, optional).
API key: TYPESAFE_API_KEY from the environment or from a `.env` line in the workspace (never printed).
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from . import store
from .collect import UA
from .store import now_utc
from .workspace import Workspace, add_argument as add_workspace_argument

API_URL = "https://api.typesafe.ai/v1/systemone"
MIN_DESCRIPTION = 300  # shorter descriptions are not scored (no Jev call); `collect show <id>` tells why
DEFAULT_DOMAIN = "the kind of work the candidate's profile is focused on"
# a must-have asking for a number of years: "3+ years", "4–6+ years", "+8 years", "minimum 2 ani"
YEARS = re.compile(r"\d+\s*\+?\s*(?:[-–/]|to)?\s*\d*\s*\+?\s*(?:years?|yrs|ani)\b", re.I)
# scoring.json keys that change what is sent to Jev; weights and thresholds only change the combining in code
REQUEST_KEYS = ("model", "max_description_chars", "max_must_have", "max_nice_to_have", "languages", "target_domain",
                "role_families", "profile_stop_heading", "reference_draft")

# ---------- requirement extraction (deterministic, no AI) ----------

REQ_HEAD = re.compile(
    r"requirement|qualification|what you('|’)?ll (need|bring)|what you bring|what (we|you) need|"
    r"what we('|’)?re looking for|looking for|who you are|about you\b|you (have|bring|are|will have|might be)\b|"
    r"your (profile|background|experience|skills)|must[- ]haves?|skills (and|&) experience|experience (and|&) skills|"
    r"ideal candidate|to be successful|minimum|key skills|what it takes|competen|cerin[tț]e|profilul|"
    # company career pages: "Required Skills", "WHAT WE'D LOVE TO SEE", "These are the essentials you'll need"
    r"\brequired\b|essentials|you('|’)?ll need|you need|you should (have|bring)|we expect|expectations|"
    r"we('|’)?d love|love to see|\bskills\b|\bexperience\b|\bknowledge\b|tech(nical)? stack|"
    r"ce (ne )?a[șs]tept[ăa]m|ce c[ăa]ut[ăa]m|ai nevoie|ar trebui s[ăa] ai",
    re.I)
NICE_HEAD = re.compile(
    r"nice[- ]to[- ]have|bonus|preferred|(a |big )?plus\b|extra credit|good to have|desirable|would be great|"
    r"even better|stand out|would help|don('|’)?t need to (tick|have)|constituie un avantaj", re.I)
# Headings that mention requirements but hold encouragement / EEO text, and boilerplate lines inside a section.
NOT_REQ_HEAD = re.compile(r"don('|’)?t meet|every (single )?requirement|even if you don('|’)?t", re.I)
BOILERPLATE = re.compile(r"equal (treatment|opportunit)|discriminat|encourage you to apply|diverse|inclusive|"
                         r"reasonable accommodation|regardless of (age|race|gender)", re.I)
# A must-have item that says of itself that it is optional.
ITEM_NICE = re.compile(r"\b(is a (big )?plus|nice to have|is a bonus|not a prerequisite|not required|is beneficial)\b", re.I)
BULLET = re.compile(r"^\s*(?:[-•*–·▪◦]|\d{1,2}[.)])\s+")


# "We offer a hybrid working model ... 3 days per week from home:" - a long lead-in that still ends the requirements
OFFER_HEAD = re.compile(r"^(we offer|what we offer|our offer|in return|benefits|perks|why (join|work)|what('|’)?s in it for you|"
                        r"oferim|beneficii|ce oferim)\b", re.I)


def is_heading(line, prev_blank):
    s = line.strip()
    if s and len(s) <= 300 and not BULLET.match(line) and OFFER_HEAD.match(s) and s.endswith((":", "!")) or \
            (s and OFFER_HEAD.match(s) and len(s) <= 60 and not BULLET.match(line)):
        return True
    if not s or len(s) > 120 or BULLET.match(line) or s.endswith((".", ",", ";")):
        return False
    letters = [c for c in s if c.isalpha()]
    if letters and all(c.isupper() for c in letters) or s.endswith(":"):
        return True
    return prev_blank and len(s) <= 80 and len(s.split()) <= 8 and s[0].isupper()


def extract_requirements(description, max_must=12, max_nice=6):
    """→ {"must": [...], "nice": [...]} from requirement-like sections; bullets first, else the section's lines."""
    must, nice = [], []
    section, items = None, []

    def close():
        if section and items:
            bullets = [i for b, i in items if b] or [i for _, i in items]
            for item in bullets:
                if BOILERPLATE.search(item):
                    continue
                target = nice if section == "nice" or NICE_HEAD.match(item) or ITEM_NICE.search(item) else must
                target.append(item)

    prev_blank = True
    for raw in (description or "").splitlines():
        line = raw.replace("\xa0", " ").rstrip()
        if not line.strip():
            prev_blank = True
            continue
        if is_heading(line, prev_blank):
            close()
            head = line.strip().rstrip(":")
            if NOT_REQ_HEAD.search(head):
                section = None
            else:
                section = "nice" if NICE_HEAD.search(head) else ("must" if REQ_HEAD.search(head) else None)
            items = []
        elif section:
            text = re.sub(r"\s+", " ", BULLET.sub("", line)).strip()
            if len(text) >= 15:
                items.append((bool(BULLET.match(line)), text[:300]))
        prev_blank = False
    close()
    if not must:  # the ad came as one flat paragraph: look for inline cues ("... you'll need to get an interview: ...")
        inline = extract_inline(description)
        must, nice = inline["must"], nice or inline["nice"]
    dedup = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    return {"must": dedup(must)[:max_must], "nice": dedup(nice)[:max_nice]}


# Flat text: cues are short phrases ending in ":" or a bare section word ("Requirements Bachelor's ...");
# items are their sentences, and run-on lists are cut where a typical requirement starts.
ITEM_START = re.compile(r"(?<! or)(?<! and)(?<=[a-z0-9)%])\s+(?=(?:Minimum|At least|Experience|Knowledge|Understanding|Familiarity|"
                        r"Proficiency|Proficient|Strong|Good|Very good|Excellent|Solid|Previous|Proven|Bachelor|Master|"
                        r"Degree|University|Ability|Fluent|Fluency|Hands-on|Working knowledge|Advanced|Expertise|"
                        r"Demonstrated|Comfortable|Familiar|Background)\b)")


def extract_inline(description):
    flat = re.sub(r"\s+", " ", (description or "").replace("\xa0", " ")).strip()
    cues = []  # (start, end, kind)
    for m in re.finditer(r"([^.!?:]{3,140}):\s+", flat):
        head = m.group(1).strip()
        kind = ("nice" if NICE_HEAD.search(head) else "must" if REQ_HEAD.search(head) and not NOT_REQ_HEAD.search(head)
                else "other")
        cues.append((m.start(1), m.end(), kind))
    for m in re.finditer(r"(?<=[a-z.!?)] )(Requirements|Qualifications|Responsibilities|Benefits|Nice to have|"
                         r"Desirable|Preferred qualifications|What we offer|We offer|Perks|About us|About the team)\s+(?=[A-Z])", flat):
        word = m.group(1)
        kind = "nice" if NICE_HEAD.search(word) else "must" if REQ_HEAD.search(word) else "other"
        cues.append((m.start(1), m.end(), kind))
    cues.sort()
    out = {"must": [], "nice": []}
    for i, (start, end, kind) in enumerate(cues):
        if kind == "other":
            continue
        stop = cues[i + 1][0] if i + 1 < len(cues) else min(len(flat), end + 1500)
        segment = flat[end:stop]
        for sentence in re.split(r"(?<=[.;!?])\s+", segment):
            for item in ITEM_START.split(sentence):
                item = item.strip(" -•;")
                if 15 <= len(item) <= 300 and not BOILERPLATE.search(item):
                    target = "nice" if kind == "nice" or ITEM_NICE.search(item) else "must"
                    out[target].append(item)
    return out


# ---------- candidate profile ----------

def md_section(text, heading):
    m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def build_profile(pool_path, draft_path=None, stop_heading="Information to collect"):
    with open(pool_path, encoding="utf-8") as f:
        pool = f.read()
    if stop_heading:
        pool = re.split(rf"^## {re.escape(stop_heading)}", pool, flags=re.M)[0].strip()
    parts = [pool]
    if draft_path and os.path.exists(draft_path):
        with open(draft_path, encoding="utf-8") as f:
            draft = f.read()
        roles = re.findall(r"^### (.+\|.+)$", md_section(draft, "Work Experience"), re.M)
        if roles:
            parts.append("## Employment history (title | employer | dates)\n" + "\n".join(f"- {r.strip()}" for r in roles))
        for name in ("Education", "Certifications and Courses", "Languages"):
            body = md_section(draft, name)
            if body:
                parts.append(f"## {name}\n{body}")
    parts.append("Note: items listed under 'Do not claim yet' are NOT evidence the candidate has that skill.")
    return "\n\n".join(parts)


# ---------- questions ----------

MET = {"true": "The profile names this skill, tool, experience or qualification, or a clear equivalent of it",
       "false": "The profile does not show it, or lists it only under 'Do not claim yet'"}
HARD = {"true": "A concrete qualification: a named tool or technology, years of experience, a degree, a certification, "
                "a spoken language, a licence or a right to work",
        "false": "A soft trait, attitude or general ability, such as curiosity, communication or ownership"}
def rubrics(cfg):
    """→ {question id: (instructions, levels low → high)}; the domain rubric uses scoring.json → target_domain."""
    d = cfg.get("target_domain") or DEFAULT_DOMAIN
    return {
        "skills_fit": ("How well the candidate's demonstrated skills cover the core technical work described in the job", [
            "The core work relies on skills and tools the candidate has not shown",
            "The candidate covers part of the core work but misses central tools or methods",
            "The candidate covers most of the core work; the gaps are adjacent tools that are quick to learn",
            "The candidate has shown nearly all of the core tools and methods the job needs",
        ]),
        "seniority_fit": ("How the experience level the job expects compares with the candidate's experience in this field", [
            "The job expects a senior, staff or lead engineer with many more years in this field than the candidate has",
            "The job expects a mid-level professional with a few more years in this field than the candidate has",
            "The job's expected experience matches the candidate's, or the job is junior or states no experience level",
        ]),
        "domain_fit": (f"How central {d} is to this job", [
            f"The job is outside {d}",
            f"The job touches {d} but is mainly a different kind of work",
            f"The job is centered on {d}",
        ]),
    }


def candidate_languages(cfg, profile):
    """scoring.json → languages, else the names in the profile's "## Languages" section ("English (advanced) | …")."""
    if cfg.get("languages"):
        return list(cfg["languages"])
    body = md_section(profile, "Languages")
    names = [re.sub(r"\(.*?\)|[-–:].*", "", part).strip() for part in re.split(r"[|,;\n]", body)]
    return [n for n in names if n and len(n) < 30]


def build_questions(reqs, cfg=None, profile=""):
    cfg = cfg or {}
    q = {}
    for kind in ("must", "nice"):
        for i, text in enumerate(reqs[kind]):
            q[f"{kind}_{i}_met"] = {"type": "noul", "criteria": MET, "instructions": {
                "statement": "The candidate profile shows evidence that satisfies this job requirement.", "requirement": text}}
            if kind == "must":
                q[f"must_{i}_hard"] = {"type": "noul", "criteria": HARD, "instructions": {
                    "statement": "This job requirement is a hard qualification rather than a soft trait.", "requirement": text}}
    langs = candidate_languages(cfg, profile)
    if langs:
        spoken = ", ".join(langs[:-1]) + (" or " if len(langs) > 1 else "") + langs[-1]
        q["language_block"] = {"type": "noul", "instructions":
                               f"The job requires fluency in a language other than {spoken}."}
    for qid, (instructions, levels) in rubrics(cfg).items():
        q[qid] = {"type": "score", "instructions": instructions, "criteria": levels}
    if cfg.get("role_families"):
        q["role_family"] = {"type": "choice", "instructions": "Which family does this job belong to",
                            "criteria": cfg["role_families"]}
    return q


def build_state(profile, job, max_chars):
    return {"candidate": profile, "job": {
        "title": job["title"], "company": job["company"], "location": job["location"],
        "description": (job["description"] or "")[:max_chars]}}


# ---------- API ----------

class JevClient:
    def __init__(self, key, model, long_context=False, timeout=90):
        self.key, self.model, self.long_context, self.timeout = key, model, long_context, timeout

    def system_one(self, state, questions):
        body = json.dumps({"model": self.model, "state": state, "questions": questions}, ensure_ascii=False).encode()
        long_ctx = self.long_context
        for attempt in range(4):
            headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "User-Agent": UA}
            if long_ctx:
                headers["TypeSafe-Danger-Zone-Long-Context"] = "true"
            try:
                req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                if e.code in (400, 413, 422) and not long_ctx and re.search(r"long|context|too large|token", detail, re.I):
                    long_ctx = True  # the state is over the normal context: retry with the long-context header
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise RuntimeError(f"Jev HTTP {e.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 3:
                    raise RuntimeError(f"Jev unreachable: {e!r}") from None
                time.sleep(2 ** attempt * 2)
        raise RuntimeError("Jev: no answer after retries")


# ---------- combining ----------

def normalized(answer, levels):
    return answer["score"] / (len(levels) - 1)


def combine(reqs, answers, cfg):
    """Jev answers → match (0-100), verdict, knockouts, review flags and per-part details. Pure function."""
    t, w = cfg["thresholds"], cfg["weights"]
    hard_w = cfg.get("hard_requirement_weight", 2.0)
    exp = cfg.get("experience_requirements") or {}
    knockouts, flags, req_rows = [], [], []
    num = den = 0.0
    for i, text in enumerate(reqs["must"]):
        met = answers[f"must_{i}_met"]["noul"]
        hard = answers[f"must_{i}_hard"]["noul"]
        is_hard = hard >= t["hard"]
        years = bool(exp) and bool(YEARS.search(text))
        weight = exp.get("weight", 1.0) if years else hard_w if is_hard else 1.0
        num, den = num + weight * met, den + weight
        req_rows.append({"kind": "must", "text": text, "met": round(met, 3), "hard": round(hard, 3)})
        if years and not exp.get("knockout", True):
            if met < t["met"]:
                flags.append(f"experience: {text[:80]}")
        elif is_hard and met < t["not_met"]:
            knockouts.append(text)
        elif is_hard and met < t["met"]:
            flags.append(f"unsure: {text[:80]}")
    nice = [answers[f"nice_{i}_met"]["noul"] for i in range(len(reqs["nice"]))]
    req_rows += [{"kind": "nice", "text": x, "met": round(m, 3)} for x, m in zip(reqs["nice"], nice)]
    lang = answers.get("language_block", {}).get("noul", 0.0)
    if lang >= t["language_knockout"]:
        knockouts.append("requires a language the candidate does not speak")
    parts = {
        "must_have_coverage": num / den if den else None,
        "nice_to_have_coverage": sum(nice) / len(nice) if nice else None,
    }
    rubric = {}
    for qid, (_, levels) in rubrics(cfg).items():
        a = answers[qid]
        parts[qid] = normalized(a, levels)
        rubric[qid] = {"score": round(a["score"], 3), "confidence": round(a.get("confidence", 1.0), 3),
                       "probabilities": a.get("probabilities")}
        if a.get("confidence", 1.0) < t["low_confidence"]:
            flags.append(f"low confidence: {qid}")
    if not reqs["must"]:
        flags.append("no requirements section found: match from rubrics only")
    used = {k: v for k, v in parts.items() if v is not None and w.get(k)}
    total_w = sum(w[k] for k in used)
    match = round(100 * sum(w[k] * v for k, v in used.items()) / total_w, 1) if total_w else 0.0
    if knockouts:
        verdict = "knockout"
    elif match >= t["apply"] and not (flags and t.get("flags_block_apply", True)):
        verdict = "apply"
    elif match >= t["review"]:
        verdict = "review"
    else:
        verdict = "low"
    family = answers.get("role_family", {}).get("choice")
    # raw answers + requirements are kept so a weights/thresholds change is recombined locally, without new API calls
    details = {"parts": {k: (round(v, 3) if v is not None else None) for k, v in parts.items()},
               "requirements": req_rows, "rubrics": rubric, "language_block": round(lang, 3),
               "reqs": reqs, "answers": answers, "combine_key": combine_key(cfg)}
    return {"match": match, "verdict": verdict, "knockouts": knockouts, "flags": flags, "role_family": family,
            "details": details}


# ---------- storage / CLI ----------

def _hash(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def score_key(profile, cfg, job):
    """Changes only when what Jev would be asked changes (profile, request settings, job text) → then rescored."""
    return _hash([profile, {k: cfg.get(k) for k in REQUEST_KEYS}, job["title"], job["description"]])


def combine_key(cfg):
    keys = [cfg.get("weights"), cfg.get("thresholds"), cfg.get("hard_requirement_weight")]
    if cfg.get("experience_requirements"):  # only when set, so existing scores keep their key
        keys.append(cfg["experience_requirements"])
    return _hash(keys)


def recombine(db, cfg):
    """Latest scores made under other weights/thresholds → recomputed from their stored answers. → rows updated."""
    ck, n = combine_key(cfg), 0
    for s in db.execute("SELECT * FROM scores WHERE id IN (SELECT MAX(id) FROM scores GROUP BY job_id)").fetchall():
        d = json.loads(s["details"] or "{}")
        if "answers" not in d or d.get("combine_key") == ck:
            continue
        r = combine(d["reqs"], d["answers"], cfg)
        db.execute("UPDATE scores SET match = ?, verdict = ?, knockouts = ?, review_flags = ?, details = ? WHERE id = ?",
                   (r["match"], r["verdict"], json.dumps(r["knockouts"], ensure_ascii=False),
                    json.dumps(r["flags"], ensure_ascii=False), json.dumps(r["details"], ensure_ascii=False), s["id"]))
        n += 1
    db.commit()
    return n


def connect(path):
    return store.connect(path)


def pending_jobs(db, profile, cfg, ids=None, force=False, limit=None):
    q = "SELECT * FROM jobs WHERE closed_at IS NULL AND status = 'new'"
    args = []
    if ids:
        q = f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(ids))})"
        args = list(ids)
    out = []
    for job in db.execute(q + " ORDER BY published_at DESC, id DESC", args):
        if not ids and len(job["description"] or "") < MIN_DESCRIPTION:
            continue  # nothing to score against (an aggregator job whose page could not be read)
        key = score_key(profile, cfg, job)
        if force or not db.execute("SELECT 1 FROM scores WHERE job_id = ? AND key = ?", (job["id"], key)).fetchone():
            out.append((job, key))
    return out[:limit] if limit else out


def score_jobs(db, cfg, client, profile, jobs):
    """Scores (job, key) pairs with `client` (anything with system_one(state, questions)) → list of result dicts."""
    def one(item):
        job, key = item
        reqs = extract_requirements(job["description"], cfg["max_must_have"], cfg["max_nice_to_have"])
        resp = client.system_one(build_state(profile, job, cfg["max_description_chars"]), build_questions(reqs, cfg, profile))
        return job, key, reqs, resp

    results = []
    with cf.ThreadPoolExecutor(max(1, cfg.get("workers", 4))) as ex:
        for fut in cf.as_completed([ex.submit(one, it) for it in jobs]):
            try:
                job, key, reqs, resp = fut.result()
            except Exception as e:
                results.append({"error": str(e)[:300]})
                continue
            r = combine(reqs, resp["answers"], cfg)
            usage = resp.get("usage") or {}
            tokens = usage.get("input_tokens")
            db.execute("INSERT INTO scores (job_id, scored_at, model, match, verdict, knockouts, review_flags, role_family, "
                       "details, input_tokens, output_tokens, key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (job["id"], now_utc(), resp.get("model"), r["match"], r["verdict"],
                        json.dumps(r["knockouts"], ensure_ascii=False), json.dumps(r["flags"], ensure_ascii=False),
                        r["role_family"], json.dumps(r["details"], ensure_ascii=False), tokens,
                        usage.get("output_tokens"), key))
            db.commit()
            results.append({**r, "job_id": job["id"], "input_tokens": tokens})
    return results


RANK_SQL = """
SELECT j.*, s.match, s.verdict, s.knockouts, s.review_flags, s.role_family, s.scored_at
FROM jobs j JOIN scores s ON s.id = (SELECT MAX(id) FROM scores WHERE job_id = j.id)
WHERE j.closed_at IS NULL AND j.status = 'new' {extra}
ORDER BY CASE s.verdict WHEN 'apply' THEN 0 WHEN 'review' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, s.match DESC
"""


def rank_line(r):
    note = ""
    if r["verdict"] == "knockout":
        note = "  ✗ " + "; ".join(json.loads(r["knockouts"]))[:90]
    elif json.loads(r["review_flags"] or "[]"):
        note = "  ? " + "; ".join(json.loads(r["review_flags"]))[:90]
    return (f"{r['id']:>5}  {r['match']:>5.1f}  {r['verdict']:<8}  {(r['company'] or '')[:16]:<16}  "
            f"{r['title'][:52]:<52}{note}")


def main(argv=None, ws=None):
    ap = argparse.ArgumentParser(prog="jobpilot score", description=__doc__.split("\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    add_workspace_argument(common)
    common.add_argument("--db")
    common.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("score", parents=[common])
    p.add_argument("--ids", nargs="+", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("rank", parents=[common])
    p.add_argument("--verdict")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("explain", parents=[common])
    p.add_argument("id", type=int)
    p = sub.add_parser("requirements", parents=[common])
    p.add_argument("id", type=int)
    a = ap.parse_args(argv)
    ws = ws or Workspace(a.workspace)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with open(a.config or ws.path("scoring"), encoding="utf-8") as f:
        cfg = json.load(f)
    db = connect(a.db or ws.path("db"))
    if a.cmd in ("score", "rank"):
        n = recombine(db, cfg)
        if n:
            print(f"RECOMBINED {n} score(s) with the current weights/thresholds (no API call)")

    if a.cmd == "requirements":
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (a.id,)).fetchone() or sys.exit(f"ERROR no job {a.id}")
        reqs = extract_requirements(job["description"], cfg["max_must_have"], cfg["max_nice_to_have"])
        print(f"{job['title']} — {job['company']}")
        for kind in ("must", "nice"):
            print(f"\n{kind.upper()} ({len(reqs[kind])})")
            for x in reqs[kind]:
                print(f"  - {x}")
        return 0

    if a.cmd == "score":
        draft = cfg.get("reference_draft")
        profile = build_profile(ws.path("skills_pool"), ws.rel(draft) if draft else None,
                                cfg.get("profile_stop_heading", "Information to collect"))
        jobs = pending_jobs(db, profile, cfg, a.ids, a.force, a.limit)
        short = db.execute("SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL AND status = 'new' "
                           "AND LENGTH(COALESCE(description, '')) < ?", (MIN_DESCRIPTION,)).fetchone()[0]
        if short and not a.ids:
            print(f"NO DESCRIPTION {short} open `new` job(s) not scored (their page gave no description; "
                  f"--ids scores them anyway)")
        if a.dry_run:
            for job, _ in jobs:
                reqs = extract_requirements(job["description"], cfg["max_must_have"], cfg["max_nice_to_have"])
                state = json.dumps(build_state(profile, job, cfg["max_description_chars"]), ensure_ascii=False)
                print(f"{job['id']:>5}  must {len(reqs['must']):>2}  nice {len(reqs['nice']):>2}  "
                      f"questions {len(build_questions(reqs, cfg, profile)):>2}  state {len(state):>6} chars  {job['title'][:60]}")
            print(f"{len(jobs)} job(s) to score — dry run, no API call")
            return 0
        if not jobs:
            print("Nothing to score (every open `new` job already has a score for this profile/config).")
            return 0
        key = ws.env("TYPESAFE_API_KEY")
        if not key:
            sys.exit("ERROR TYPESAFE_API_KEY is not set (environment or .env in the workspace). "
                     "Create a key at https://console.typesafe.ai/keys")
        client = JevClient(key, cfg["model"], cfg.get("long_context", False))
        results = score_jobs(db, cfg, client, profile, jobs)
        errors = [r["error"] for r in results if "error" in r]
        ok = [r for r in results if "error" not in r]
        for e in errors[:5]:
            print(f"ERROR {e}")
        ids = [r["job_id"] for r in ok]
        if ids:
            rows = db.execute(RANK_SQL.format(extra=f"AND j.id IN ({','.join('?' * len(ids))})"), ids).fetchall()
            for r in rows:
                print(rank_line(r))
        tokens = sum(r["input_tokens"] or 0 for r in ok)
        print(f"\nSCORED {len(ok)}  errors {len(errors)}  input tokens {tokens}")
        return 1 if errors and not ok else 0

    if a.cmd == "rank":
        extra, args = "", []
        if a.verdict:
            extra, args = "AND s.verdict = ?", [a.verdict]
        rows = db.execute(RANK_SQL.format(extra=extra) + " LIMIT ?", args + [a.limit]).fetchall()
        if a.json:
            print(json.dumps([{k: r[k] for k in ("id", "company", "title", "location", "url", "apply_url", "match",
                                                 "verdict", "knockouts", "review_flags", "role_family")} for r in rows],
                             ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(rank_line(r))
            print(f"{len(rows)} scored open job(s)")
        return 0

    if a.cmd == "explain":
        r = db.execute("SELECT j.title, j.company, j.url, s.* FROM scores s JOIN jobs j ON j.id = s.job_id "
                       "WHERE s.job_id = ? ORDER BY s.id DESC LIMIT 1", (a.id,)).fetchone()
        if not r:
            sys.exit(f"ERROR job {a.id} has no score")
        d = json.loads(r["details"])
        print(f"{r['title']} — {r['company']}\n{r['url']}\nmatch {r['match']}  verdict {r['verdict']}  "
              f"family {r['role_family']}  scored {r['scored_at']}\n")
        for k, v in d["parts"].items():
            print(f"  {k:<22} {'-' if v is None else f'{v:.2f}'}")
        print()
        for q in d["requirements"]:
            hard = f"hard {q['hard']:.2f}" if "hard" in q else "nice     "
            print(f"  {q['kind']:<4}  met {q['met']:.2f}  {hard}  {q['text'][:110]}")
        print()
        for k, v in d["rubrics"].items():
            print(f"  {k:<14} score {v['score']:.2f}  confidence {v['confidence']:.2f}")
        print(f"  language_block {d['language_block']:.2f}")
        for label, col in (("KNOCKOUTS", "knockouts"), ("REVIEW", "review_flags")):
            items = json.loads(r[col] or "[]")
            if items:
                print(f"\n{label}\n" + "\n".join(f"  - {x}" for x in items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
