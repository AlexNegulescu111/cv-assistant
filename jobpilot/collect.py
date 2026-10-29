"""Collect today's jobs from the public job-board APIs (Greenhouse, Lever, Ashby), filter them and keep them in SQLite.

Usage (every command takes --workspace and --db; paths default to the workspace, see workspace.py):
    python -m jobpilot collect run   [--config job_sources.json] [--dry-run] [--json]
        → fetches every board in the config, upserts the jobs, re-filters jobs still `new`/`filtered_out`/`duplicate`,
          dedups reposts and cross-board copies, marks jobs already in applied.csv, marks jobs gone from a board as
          closed, and prints the new matches.
    python -m jobpilot collect list  [--status new] [--since YYYY-MM-DD] [--all] [--limit N] [--json]
        → open jobs with that status (default `new`), newest first; --all also shows closed ones.
    python -m jobpilot collect show  <id|uid>
        → the job as job.md text (title, company, location, links, description), ready for cv-for-job.
    (status changes: python -m jobpilot mark, see pipeline.py)

Public, unauthenticated endpoints only (no scraping, no login):
    Greenhouse  https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true
    Lever       https://api.lever.co/v0/postings/<board>?mode=json   (falls back to api.eu.lever.co)
    Ashby       https://api.ashbyhq.com/posting-api/job-board/<board>?includeCompensation=true

Dedup: one row per (source, board, job id); a job whose company+title+location fingerprint matches an older
open or applied row is stored as `duplicate` (reposts of a job already applied to are never `new` again; when the
original closes, the copy is filtered again).
A board that fails to fetch never closes its jobs.
"""
import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

from . import store
from .store import now_utc
from .workspace import Workspace, add_argument as add_workspace_argument

UA = "Mozilla/5.0 (compatible; job-collector/1.0; personal job search)"
FILTERABLE = ("new", "filtered_out", "duplicate")  # re-evaluated every run; later statuses are never touched


# ---------- text helpers ----------

class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(s):
    if not s:
        return ""
    p = _Text()
    p.feed(s)
    text = "".join(p.out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fold(s):
    """Lowercase, strip diacritics (București → bucuresti), collapse spaces."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s).strip()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")


def term_re(terms):
    """Whole-word match for each term ('ai' does not match 'maintain'); None when the list is empty."""
    terms = [fold(t) for t in terms or [] if t.strip()]
    if not terms:
        return None
    return re.compile("|".join(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])" for t in terms))


def iso_utc(value):
    """ISO string / epoch ms → 'YYYY-MM-DDTHH:MM:SSZ' (None when missing or unparsable)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            d = dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)
        else:
            d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None




def canonical_url(u):
    """Host + path, lowercase, no query/fragment, no trailing /apply or /application (matches applied.csv rows)."""
    if not u:
        return ""
    p = urlparse(u.strip())
    path = re.sub(r"/(apply|application)/?$", "", p.path.rstrip("/"), flags=re.I)
    return (p.netloc.lower() + path).lower()


def fingerprint(company, title, location):
    return "|".join((slug(company), slug(title), slug(location)))


# ---------- fetching ----------

def http_json(url, timeout=30, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_greenhouse(board):
    data = http_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        out.append({
            "job_id": str(j["id"]), "company": j.get("company_name"), "title": j.get("title") or "",
            "location": loc, "remote": 1 if re.search(r"\bremote\b", loc, re.I) else None, "workplace": None,
            "department": ", ".join(d.get("name", "") for d in j.get("departments") or []) or None,
            "employment_type": None, "url": j.get("absolute_url"), "apply_url": j.get("absolute_url"),
            "published_at": iso_utc(j.get("first_published") or j.get("updated_at")),
            "description": html_to_text(html.unescape(j.get("content") or "")),
        })
    return out


def fetch_lever(board):
    try:
        data = http_json(f"https://api.lever.co/v0/postings/{quote(board)}?mode=json")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        data = http_json(f"https://api.eu.lever.co/v0/postings/{quote(board)}?mode=json")
    out = []
    for j in data:
        cat = j.get("categories") or {}
        locs = [cat.get("location")] + list(cat.get("allLocations") or [])
        loc = " / ".join(dict.fromkeys(l for l in locs if l))
        wp = (j.get("workplaceType") or "").lower() or None
        parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
        for lst in j.get("lists") or []:
            parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
        parts.append(j.get("additionalPlain") or html_to_text(j.get("additional")))
        out.append({
            "job_id": str(j["id"]), "company": None, "title": j.get("text") or "", "location": loc,
            "remote": 1 if wp == "remote" else (0 if wp in ("onsite", "on-site", "hybrid") else None), "workplace": wp,
            "department": cat.get("team") or cat.get("department"), "employment_type": cat.get("commitment"),
            "url": j.get("hostedUrl"), "apply_url": j.get("applyUrl"), "published_at": iso_utc(j.get("createdAt")),
            "description": "\n\n".join(p.strip() for p in parts if p and p.strip()),
        })
    return out


def fetch_ashby(board):
    data = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        secondary = [s.get("location") for s in j.get("secondaryLocations") or [] if s.get("location")]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        locs = [j.get("location")] + secondary
        loc = " / ".join(dict.fromkeys(l for l in locs if l))
        if country and not j.get("isRemote") and fold(country) not in fold(loc):  # remote jobs carry the HQ country
            loc = f"{loc} ({country})" if loc else country
        wp = (j.get("workplaceType") or "").lower() or None
        out.append({
            "job_id": str(j["id"]), "company": None, "title": j.get("title") or "", "location": loc,
            "remote": 1 if j.get("isRemote") else (0 if wp in ("onsite", "hybrid") else None), "workplace": wp,
            "department": j.get("department") or j.get("team"), "employment_type": j.get("employmentType"),
            "url": j.get("jobUrl"), "apply_url": j.get("applyUrl"), "published_at": iso_utc(j.get("publishedAt")),
            "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
        })
    return out


def fetch_peviitor(board, **opts):
    from .peviitor import fetch_peviitor as fetch  # peviitor.py imports this module
    return fetch(board, **opts)


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "peviitor": fetch_peviitor}
PARTIAL = ("peviitor",)  # search results, not a full board: a job missing from one run is not closed


# ---------- filters ----------

class Filters:
    """Config keys (all optional):
    title_include / title_exclude   whole-word terms on the title (include: at least one must match)
    description_exclude             whole-word terms that drop a job when found in the description (e.g. "us citizenship")
    locations                       a job passes when its location matches one of these (diacritics ignored)
    allow_remote, remote_regions    a remote job passes when its location names no place, or names one of these
    workplaces                      ["remote", "hybrid", "onsite"]: drop jobs whose stated workplace is none of these
                                    (jobs that state no workplace are kept)
    max_age_days                    drop jobs published earlier than this (jobs without a date are kept)
    """
    WORKPLACES = ("remote", "hybrid", "onsite")

    def __init__(self, cfg):
        cfg = cfg or {}
        self.include = term_re(cfg.get("title_include"))
        self.exclude = term_re(cfg.get("title_exclude"))
        self.desc_exclude = term_re(cfg.get("description_exclude"))
        self.locations = term_re(cfg.get("locations"))
        self.allow_remote = cfg.get("allow_remote", True)
        self.remote_regions = term_re(cfg.get("remote_regions"))
        self.max_age_days = cfg.get("max_age_days")
        self.workplaces = {w for w in (cfg.get("workplaces") or []) if w in self.WORKPLACES} or None

    @staticmethod
    def workplace(job):
        """→ remote / hybrid / onsite, or None when the job does not say."""
        w = re.sub(r"[^a-z]", "", fold(job.get("workplace")))
        if w in ("remote", "hybrid", "onsite"):
            return w
        return "remote" if job.get("remote") == 1 else None

    def location_ok(self, job):
        if self.locations is None and self.remote_regions is None:
            return True
        loc = fold(job.get("location"))
        if self.locations and self.locations.search(loc):
            return True
        if self.allow_remote and self.remote_regions and self.remote_regions.search(loc):
            return True  # a region as the location ("EMEA", "Remote - Europe") means hiring across it
        remote = job.get("remote") == 1 or re.search(r"(?<![a-z])(remote|anywhere|worldwide)(?![a-z])", loc)
        if remote and self.allow_remote:
            # "Remote" alone or "Remote, Global" passes; "Remote, France" is a France-only remote job
            rest = re.sub(r"(?<![a-z])(remote|fully remote|anywhere|worldwide|global)(?![a-z])|[^a-z]+", " ", loc).strip()
            return not rest or bool(re.search(r"(?<![a-z])(anywhere|worldwide|global)(?![a-z])", loc))
        return False

    def check(self, job, today=None):
        """→ None when the job passes, else a short reason."""
        title = fold(job.get("title"))
        if self.include and not self.include.search(title):
            return "title: no include term"
        if self.exclude and (m := self.exclude.search(title)):
            return f"title: excluded '{m.group(0)}'"
        if not self.location_ok(job):
            return f"location: {job.get('location') or '-'}"
        if self.workplaces and (w := self.workplace(job)) and w not in self.workplaces:
            return f"workplace: {w}"
        if self.max_age_days and job.get("published_at"):
            today = today or dt.datetime.now(dt.timezone.utc)
            published = dt.datetime.fromisoformat(job["published_at"].replace("Z", "+00:00"))
            if (today - published).days > self.max_age_days:
                return f"age: published {job['published_at'][:10]}"
        if self.desc_exclude and (m := self.desc_exclude.search(fold(job.get("description")))):
            return f"description: excluded '{m.group(0)}'"
        return None


# ---------- storage ----------

def board_entries(config, with_options=False):
    """config["boards"][source] items: "slug" or {"board": "slug", "company": "Name", <fetcher options>}
    (peviitor: {"board": "<search words>", "city": "...", "rows": 50, "pages": 2})."""
    for source, items in (config.get("boards") or {}).items():
        if source not in FETCHERS:
            print(f"WARN unknown source '{source}' in config (known: {', '.join(FETCHERS)})", file=sys.stderr)
            continue
        for item in items or []:
            if isinstance(item, str):
                item = {"board": item}
            opts = {k: v for k, v in item.items() if k not in ("board", "company")}
            entry = (source, item["board"], item.get("company") or item["board"])
            yield entry + (opts,) if with_options else entry


def configured_boards(config):
    """{source: {slug, ...}} of the ATS boards collected through their APIs."""
    out = {}
    for source, board, _ in board_entries(config):
        if source not in PARTIAL:
            out.setdefault(source, set()).add(board)
    return out


def route_partial(db, source, board, jobs, config, seen_at):
    """peviitor results → the jobs to store (company sites, other ATS); board suggestions are recorded.
    → (kept jobs, counts by route)."""
    from . import peviitor
    boards, kept, counts = configured_boards(config), [], {}
    for j in jobs:
        kind, detail = peviitor.route(j["url"], boards)
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "keep":
            j["department"] = detail  # the ATS name or "company site", shown in `list`
            kept.append(j)
        elif kind == "suggest":
            src, slug = detail
            db.execute("INSERT INTO board_suggestions (source, board, company, jobs_seen, example_url, found_by, "
                       "first_seen, last_seen) VALUES (?, ?, ?, 1, ?, ?, ?, ?) ON CONFLICT(source, board) DO UPDATE SET "
                       "jobs_seen = jobs_seen + 1, last_seen = excluded.last_seen",
                       (src, slug, j.get("company"), j["url"], f"{source}:{board}", seen_at, seen_at))
    return kept, counts


def describe_new(db, limit=40, workers=6):
    """Jobs from partial sources that passed the filters but have no description → read it from the job page.
    → number described."""
    from . import peviitor
    rows = db.execute(f"SELECT id, url FROM jobs WHERE source IN ({','.join('?' * len(PARTIAL))}) AND status = 'new' "
                      "AND closed_at IS NULL AND COALESCE(description, '') = '' LIMIT ?", (*PARTIAL, limit)).fetchall()
    db.commit()  # the page reads below take a while: release the write lock first
    with cf.ThreadPoolExecutor(max(1, min(workers, len(rows) or 1))) as ex:
        texts = list(ex.map(lambda r: peviitor.describe(r["url"]), rows))
    n = 0
    for r, text in zip(rows, texts):
        if text:
            db.execute("UPDATE jobs SET description = ? WHERE id = ?", (text, r["id"]))
            n += 1
    return n


def load_applied_urls(path):
    urls = set()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("status") or "") in ("submitted", "already_applied"):
                    urls.update(canonical_url(row.get(k)) for k in ("job_url", "form_url") if row.get(k))
    urls.discard("")
    return urls


def upsert(db, source, board, company, job, seen_at, run_id):
    """→ True when the job is new to the database."""
    uid = f"{source}:{board}:{job['job_id']}"
    company = job.get("company") or company
    # aggregators list one posting once per city (separate links): without the location they collapse into one
    fp = fingerprint(company, job["title"], "" if source in PARTIAL else job.get("location"))
    cols = ("company", "title", "location", "remote", "workplace", "department", "employment_type",
            "url", "apply_url", "published_at", "description")
    vals = [company] + [job.get(c) for c in cols[1:]]
    row = db.execute("SELECT id FROM jobs WHERE uid = ?", (uid,)).fetchone()
    if row:
        db.execute(f"UPDATE jobs SET {', '.join(c + ' = ?' for c in cols)}, fingerprint = ?, last_seen = ?, last_run = ?, "
                   "closed_at = NULL WHERE id = ?", vals + [fp, seen_at, run_id, row["id"]])
        return False
    db.execute(f"INSERT INTO jobs (uid, source, board, job_id, {', '.join(cols)}, fingerprint, first_seen, last_seen, last_run) "
               f"VALUES ({', '.join('?' * (len(cols) + 8))})",
               [uid, source, board, job["job_id"]] + vals + [fp, seen_at, seen_at, run_id])
    return True


def apply_states(db, filters, applied_urls, today=None):
    """Re-evaluates open jobs still `new`/`filtered_out`/`duplicate`: applied.csv → applied, fingerprint copy → duplicate, filters."""
    rows = db.execute(f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(FILTERABLE))}) AND closed_at IS NULL "
                      "ORDER BY id", FILTERABLE).fetchall()
    for r in rows:
        job = dict(r)
        if canonical_url(job["url"]) in applied_urls or canonical_url(job["apply_url"]) in applied_urls:
            db.execute("UPDATE jobs SET status = 'applied', reason = 'in applied.csv' WHERE id = ?", (job["id"],))
            continue
        twin = db.execute("SELECT id FROM jobs WHERE fingerprint = ? AND id < ? AND (closed_at IS NULL OR status = 'applied') "
                          "AND status != 'duplicate' ORDER BY id LIMIT 1", (job["fingerprint"], job["id"])).fetchone()
        if twin:
            db.execute("UPDATE jobs SET status = 'duplicate', duplicate_of = ?, reason = 'same company+title+location' "
                       "WHERE id = ?", (twin["id"], job["id"]))
            continue
        reason = filters.check(job, today)
        db.execute("UPDATE jobs SET status = ?, reason = ? WHERE id = ?",
                   ("filtered_out" if reason else "new", reason, job["id"]))


def collect(db, config, applied_path=None, fetchers=None, workers=8, today=None):
    """One collection run. → summary dict (per-board counts, errors, ids of jobs that became `new` in this run)."""
    fetchers = fetchers or FETCHERS
    started = now_utc()
    run_id = db.execute("INSERT INTO runs (started_at) VALUES (?)", (started,)).lastrowid
    db.commit()  # no write lock held during the network fetches (other processes share the db)
    entries = list(board_entries(config, with_options=True))
    results = {}
    with cf.ThreadPoolExecutor(max(1, min(workers, len(entries) or 1))) as ex:
        futures = {(ex.submit(fetchers[s], b, **o) if o else ex.submit(fetchers[s], b)): (s, b, c) for s, b, c, o in entries}
        for fut in cf.as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception as e:  # one broken board never stops the run
                results[key] = e
    before_new = {r["id"] for r in db.execute("SELECT id FROM jobs WHERE status = 'new' AND closed_at IS NULL")}
    summary = {"started_at": started, "boards": [], "errors": []}
    for (source, board, company), res in sorted(results.items()):
        if isinstance(res, Exception):
            summary["errors"].append(f"{source}:{board}: {res!r}"[:200])
            continue
        routes = None
        if source in PARTIAL:
            fetched = len(res)
            res, routes = route_partial(db, source, board, res, config, started)
        else:
            fetched = len(res)
        inserted = sum(upsert(db, source, board, company, j, started, run_id) for j in res)
        closed = 0 if source in PARTIAL else db.execute(
            "UPDATE jobs SET closed_at = ? WHERE source = ? AND board = ? AND closed_at IS NULL AND last_run < ?",
            (started, source, board, run_id)).rowcount
        summary["boards"].append({"board": f"{source}:{board}", "fetched": fetched, "inserted": inserted, "closed": closed,
                                  **({"routes": routes} if routes is not None else {})})
    filters, applied_urls = Filters(config.get("filters")), load_applied_urls(applied_path)
    apply_states(db, filters, applied_urls, today)
    if any(s in PARTIAL for s, *_ in entries):
        summary["described"] = describe_new(db, config.get("describe_limit", 150))
        apply_states(db, filters, applied_urls, today)  # description_exclude now sees the text
    summary["suggestions"] = db.execute("SELECT COUNT(*) FROM board_suggestions WHERE status = 'pending'").fetchone()[0]
    now_new = [r["id"] for r in db.execute("SELECT id FROM jobs WHERE status = 'new' AND closed_at IS NULL "
                                           "ORDER BY published_at DESC, id DESC")]
    summary["new_matches"] = [i for i in now_new if i not in before_new]
    summary["open_new_total"] = len(now_new)
    summary["finished_at"] = now_utc()
    db.execute("UPDATE runs SET finished_at = ?, summary = ? WHERE id = ?", (summary["finished_at"], json.dumps(summary), run_id))
    db.commit()
    return summary


# ---------- CLI ----------

def find_rows(db, refs):
    rows = []
    for ref in refs:
        r = db.execute("SELECT * FROM jobs WHERE id = ? OR uid = ?", (int(ref) if ref.isdigit() else -1, ref)).fetchone()
        if not r:
            sys.exit(f"ERROR no job '{ref}'")
        rows.append(r)
    return rows


def row_line(r):
    date = (r["published_at"] or "")[:10] or "-"
    return f"{r['id']:>5}  {date}  {(r['company'] or '')[:18]:<18}  {r['title'][:60]:<60}  {(r['location'] or '-')[:40]}"


def as_job_md(r):
    return (f"# {r['title']}\n\nCompany: {r['company']}\nLocation: {r['location'] or '-'}"
            f"{' (remote)' if r['remote'] == 1 else ''}\nPublished: {(r['published_at'] or '-')[:10]}\n"
            f"Source: {r['source']} ({r['board']})\nJob URL: {r['url']}\nApply URL: {r['apply_url']}\n\n"
            f"## Description\n\n{r['description'] or ''}\n")


def main(argv=None, ws=None):
    ap = argparse.ArgumentParser(prog="jobpilot collect", description=__doc__.split("\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    add_workspace_argument(common)
    common.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", parents=[common])
    p.add_argument("--config")
    p.add_argument("--applied-log")
    p.add_argument("--dry-run", action="store_true", help="fetch and filter into an in-memory db; nothing is saved")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("list", parents=[common])
    p.add_argument("--status", default="new")
    p.add_argument("--since", help="published on/after YYYY-MM-DD")
    p.add_argument("--all", action="store_true", help="include closed jobs")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", parents=[common])
    p.add_argument("ref")
    p = sub.add_parser("suggest", parents=[common])
    p.add_argument("--config")
    p.add_argument("--add", nargs="+", metavar="SOURCE:BOARD", help="add these boards to job_sources.json")
    p.add_argument("--ignore", nargs="+", metavar="SOURCE:BOARD", help="never suggest these again")
    p.add_argument("--all", action="store_true", help="also added / ignored suggestions")
    a = ap.parse_args(argv)
    ws = ws or Workspace(a.workspace)
    db_path = a.db or ws.path("db")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if a.cmd == "run":
        with open(a.config or ws.path("job_sources"), encoding="utf-8") as f:
            config = json.load(f)
        db = store.connect(":memory:" if a.dry_run else db_path)
        s = collect(db, config, a.applied_log or ws.path("applied_log"))
        if a.json:
            s["new_matches"] = [dict(r) for r in db.execute(
                f"SELECT id, uid, company, title, location, remote, published_at, url, apply_url FROM jobs "
                f"WHERE id IN ({','.join('?' * len(s['new_matches']))})", s["new_matches"])] if s["new_matches"] else []
            print(json.dumps(s, ensure_ascii=False, indent=2))
            return 0
        for b in s["boards"]:
            routes = "  " + " ".join(f"{k} {v}" for k, v in sorted(b["routes"].items())) if b.get("routes") else ""
            print(f"{b['board'][:32]:<32} fetched {b['fetched']:>4}  new {b['inserted']:>4}  closed {b['closed']:>3}{routes}")
        for e in s["errors"]:
            print(f"ERROR {e}")
        if "described" in s:
            print(f"DESCRIBED {s['described']} job page(s) read for their description")
        if s.get("suggestions"):
            print(f"SUGGESTED BOARDS {s['suggestions']} pending (python -m jobpilot collect suggest)")
        print(f"\nNEW MATCHES {len(s['new_matches'])} (open `new` total {s['open_new_total']})"
              f"{' — dry run, nothing saved' if a.dry_run else ''}")
        for i in s["new_matches"]:
            print(row_line(db.execute("SELECT * FROM jobs WHERE id = ?", (i,)).fetchone()))
        return 1 if s["errors"] and not s["boards"] else 0

    db = store.connect(db_path)
    if a.cmd == "list":
        q, args = "SELECT * FROM jobs WHERE status = ?", [a.status]
        if not a.all:
            q += " AND closed_at IS NULL"
        if a.since:
            q += " AND published_at >= ?"
            args.append(a.since)
        rows = db.execute(q + " ORDER BY published_at DESC, id DESC LIMIT ?", args + [a.limit]).fetchall()
        if a.json:
            print(json.dumps([{k: r[k] for k in r.keys() if k != "description"} for r in rows], ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(row_line(r) + (f"  [{r['reason']}]" if r["reason"] and a.status != "new" else ""))
            print(f"{len(rows)} job(s) with status '{a.status}'")
    elif a.cmd == "show":
        print(as_job_md(find_rows(db, [a.ref])[0]))
    elif a.cmd == "suggest":
        return suggest(db, a.config or ws.path("job_sources"), a.add, a.ignore, a.all)
    return 0


def suggest(db, config_path, add=None, ignore=None, show_all=False):
    """ATS boards seen through an aggregator. --add writes them into job_sources.json (json module, same layout)."""
    def split(ref):
        source, _, board = ref.partition(":")
        if source not in FETCHERS or source in PARTIAL or not board:
            sys.exit(f"ERROR '{ref}' is not <greenhouse|lever|ashby>:<board>")
        return source, board

    if add:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        have = configured_boards(config)
        for ref in add:
            source, board = split(ref)
            row = db.execute("SELECT company FROM board_suggestions WHERE source = ? AND board = ?", (source, board)).fetchone()
            if board not in have.get(source, set()):
                entry = {"board": board}
                if row and row["company"]:
                    entry["company"] = row["company"]
                config.setdefault("boards", {}).setdefault(source, []).append(entry)
            db.execute("UPDATE board_suggestions SET status = 'added' WHERE source = ? AND board = ?", (source, board))
            print(f"ADDED {source}:{board} → {config_path}")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")
    for ref in ignore or []:
        source, board = split(ref)
        db.execute("UPDATE board_suggestions SET status = 'ignored' WHERE source = ? AND board = ?", (source, board))
        print(f"IGNORED {source}:{board}")
    db.commit()
    q = "SELECT * FROM board_suggestions" + ("" if show_all else " WHERE status = 'pending'") + " ORDER BY jobs_seen DESC"
    rows = db.execute(q).fetchall()
    for r in rows:
        print(f"{r['source'] + ':' + r['board']:<40} jobs seen {r['jobs_seen']:>3}  {(r['company'] or '')[:30]:<30}  "
              f"[{r['status']}]  {r['example_url']}")
    print(f"{len(rows)} suggestion(s)" + ("" if show_all else " pending - add with: suggest --add <source:board> ..."))
    return 0
