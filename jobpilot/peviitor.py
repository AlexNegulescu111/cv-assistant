"""peviitor.ro as a discovery source: search the aggregator, then route every job by where its link goes.

Read-only public endpoints only (https://api.peviitor.ro):
    GET /v1/search/?q=<words>&city=<city>&page=<n>&rows=<n>   → response.docs[] {title, company, city, location,
                                                                 workmode, url, date, tags, cif}
    GET /v1/random/                                             (used by `sites probe --peviitor`)
Endpoints that write (upload / update / delete / add company) are never called.

Routing of a job link (route()):
    covered     - a Greenhouse / Lever / Ashby board already in job_sources.json → skipped (it comes from the ATS API,
                  with the full description)
    suggest     - a Greenhouse / Lever / Ashby board not in job_sources.json → skipped and recorded as a board
                  suggestion (`collect suggest`); once added, all of that company's jobs come from the ATS API
    aggregator  - another job board / aggregator (anofm, bestjobs, ejobs ...) → skipped: not the employer's form
    blocked     - LinkedIn → skipped, never opened
    keep        - a company career site or another ATS (Workday, SmartRecruiters, Teamtailor ...) → stored as a
                  `peviitor` job; its description is read from the job page (describe()) before filtering
"""
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request

from .collect import UA, html_to_text, iso_utc
from .sites import AGGREGATORS, host_matches, host_of

API = "https://api.peviitor.ro"
BOARD_URL = [  # job link → (source, board slug) for the ATS the collector reads through its public API
    ("ashby", re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#]+)", re.I)),
    ("lever", re.compile(r"^https?://jobs(?:\.eu)?\.lever\.co/([^/?#]+)", re.I)),
    ("greenhouse", re.compile(r"^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?#&]+)", re.I)),
]
OTHER_ATS = {"myworkdayjobs.com": "Workday", "smartrecruiters.com": "SmartRecruiters", "teamtailor.com": "Teamtailor",
             "workable.com": "Workable", "recruitee.com": "Recruitee", "personio.de": "Personio", "personio.com": "Personio",
             "bamboohr.com": "BambooHR", "breezy.hr": "Breezy", "icims.com": "iCIMS", "taleo.net": "Taleo",
             "successfactors.com": "SuccessFactors", "successfactors.eu": "SuccessFactors", "jobvite.com": "Jobvite",
             "oraclecloud.com": "Oracle Recruiting", "avature.net": "Avature", "comeet.com": "Comeet"}
BLOCKED = ("linkedin.com", "lnkd.in")


def http_json(path, params, timeout=30):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_peviitor(board, city=None, rows=50, pages=2, **_):
    """One search ("board" = the query words) → normalized jobs; `route` decides later what is kept."""
    jobs, seen = [], set()
    for page in range(1, int(pages) + 1):
        params = {"q": board, "page": page, "rows": rows}
        if city:
            params["city"] = city
        docs = (http_json("/v1/search/", params).get("response") or {}).get("docs") or []
        for d in docs:
            url = d.get("url")
            url = url[0] if isinstance(url, list) else url
            if not url or url in seen:
                continue
            seen.add(url)
            wm = (d.get("workmode") or d.get("remote") or "").lower()
            cities = d.get("city") or d.get("location") or []
            cities = cities if isinstance(cities, list) else [cities]
            jobs.append({
                "job_id": hashlib.sha1(url.encode()).hexdigest()[:16], "company": html.unescape(d.get("company") or ""),
                "title": html.unescape(d.get("title") or ""), "location": " / ".join(dict.fromkeys(c for c in cities if c)) +
                (" (remote)" if wm == "remote" else ""),
                "remote": 1 if wm == "remote" else (0 if wm in ("on-site", "onsite", "hybrid") else None),
                "workplace": wm or None, "department": None, "employment_type": None,
                "url": url, "apply_url": url, "published_at": iso_utc(d.get("date")), "description": "",
            })
        if len(docs) < int(rows):
            break
    return jobs


def route(url, boards):
    """→ (kind, detail). `boards`: {source: {slug, ...}} already collected through the ATS APIs."""
    host = host_of(url)
    if host_matches(host, BLOCKED):
        return "blocked", host
    for source, rx in BOARD_URL:
        m = rx.match(url or "")
        if m:
            slug = urllib.parse.unquote(m.group(1))
            if slug.lower() in {b.lower() for b in boards.get(source, ())}:
                return "covered", f"{source}:{slug}"
            return "suggest", (source, slug)
    if host_matches(host, AGGREGATORS):
        return "aggregator", host
    ats = next((name for key, name in OTHER_ATS.items() if host == key or host.endswith("." + key)), None)
    return "keep", ats or "company site"


WORKDAY_JOB = re.compile(r"^https://([^/]+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/(job/.+?)/?(?:[?#].*)?$")


def workday_description(url, timeout=25):
    """Workday career pages are built by JavaScript; the site's own public JSON endpoint has the ad as HTML."""
    m = WORKDAY_JOB.match(url or "")
    if not m:
        return ""
    host, site, path = m.groups()
    tenant = host.split(".")[0]
    try:
        req = urllib.request.Request(f"https://{host}/wday/cxs/{tenant}/{site}/{path}",
                                     headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            info = json.load(r).get("jobPostingInfo") or {}
    except Exception:
        return ""
    return html_to_text(info.get("jobDescription") or "")


def describe(url, timeout=25, max_chars=20000):
    """The job description from the job page without a browser: Workday's JSON endpoint, JSON-LD JobPosting or the
    page's main text (whichever keeps the structure). → text ('' when the page needs JavaScript or the request fails)."""
    wd = workday_description(url, timeout)
    if len(wd) > 400:
        return wd[:max_chars]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en,ro;q=0.8"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""
    ld = ""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', raw, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                stack.extend(d.get("@graph") or [])
                t = d.get("@type")
                if (t == "JobPosting" or isinstance(t, list) and "JobPosting" in t) and d.get("description") and not ld:
                    ld = html_to_text(html.unescape(d["description"]) if "&lt;" in d["description"] else d["description"])
    body = re.sub(r"(?is)<(script|style|noscript|svg|nav|header|footer)[^>]*>.*?</\1>", " ", raw)
    m = re.search(r"(?is)<(main|article)[^>]*>(.*?)</\1>", body)
    page = html_to_text(m.group(2) if m else body)
    # JSON-LD is often the ad flattened into one paragraph; the page keeps its headings and bullets → prefer the page
    # when it is about as long and much more structured
    if ld and not (structured(page) and not structured(ld) and len(page) >= 0.8 * len(ld)):
        return ld[:max_chars]
    return page[:max_chars] if len(page) > 400 else ld[:max_chars]


def structured(text):
    """At least one line break per ~400 characters (headings / bullets survived)."""
    return text.count("\n") >= max(5, len(text) // 400)
