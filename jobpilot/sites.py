"""Site template library: how each ATS / career site's apply flow works - structure only, never answers.

A template says how to get from a job page to its form and what the form does:
    id, kind ("ats" shipped with the package | "site" learned for one career-site host), name,
    match {"hosts": [host suffixes], "markers": [substrings of the page source]},
    description_selector, apply_text, guest_method, next_text, submit_text, confirmation [regex],
    login ("required" | "optional" | "none" | null), captcha (true | false | null),
    fields [{label, type, required}] (the last form seen), notes [text],
    stats {extracts, fills, submitted, needs_human, last_seen}, verified (a submit was confirmed on it).
Templates hold labels, selectors and button texts of the site - never a person's answers, so ATS templates are
shared in the package (jobpilot/sites/*.json) and learned site templates (workspace site_templates/) can be shared
after review.

page.py uses them: extract tries the template's description selector / Apply text / guest method first and learns
the site (new host → new "site" template); fill prefers the template's submit text, accepts its confirmation
phrases and learns the result (submit label, confirmation phrase, CAPTCHA/login seen, verified on a confirmed
submit). Learned ATS stats go to site_templates/_stats.json, so the shipped ATS templates stay unchanged.

Usage:
    python -m jobpilot sites list
    python -m jobpilot sites show <id>
    python -m jobpilot sites match <url>
    python -m jobpilot sites probe [<url> ...] [--peviitor N] [--out DIR]
        → extracts each job page (no form is filled), then reports per site: template before / after, description,
          fields, CV upload, submit button, CAPTCHA, login. --peviitor N samples N random jobs from the peviitor.ro
          public API (GET /v1/random/) and keeps the ones on company career sites.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
from urllib.parse import urlparse

PACKAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites")
STATS_FILE = "_stats.json"
KEYS = ("description_selector", "apply_text", "guest_method", "next_text", "submit_text")
# Job boards / aggregators: their pages are listings, not the employer's form (probe reports them apart).
AGGREGATORS = ("anofm.ro", "multijobs.ro", "oferimdemunca.ro", "jobviewtrack.com", "jobradar24.ro", "dreamjobs.ro",
               "publi24.ro", "ejobs.ro", "iajob.ro", "bestjobs.eu", "hipo.ro", "olx.ro", "jooble.org", "indeed.com",
               "linkedin.com", "lnkd.in", "glassdoor.com", "jobs.ro", "teamland.ro", "leohr.ro", "careerjet.ro",
               "undelucram.ro", "8ore.ro", "targuldecariere.ro")


def host_of(url):
    host = urlparse(url or "").netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def site_id(url):
    return "site:" + host_of(url)


def host_matches(host, suffixes):
    return any(host == s or host.endswith("." + s) or (s.endswith(".") and host.startswith(s)) for s in suffixes)


def is_aggregator(url):
    return host_matches(host_of(url), AGGREGATORS)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _file_for(ws_dir, tid):
    return os.path.join(ws_dir, re.sub(r"[^a-z0-9._-]+", "_", tid.lower()) + ".json")


class Library:
    def __init__(self, ws_dir=None, package_dir=PACKAGE_DIR):
        self.ws_dir, self.templates = ws_dir, {}
        for d in (package_dir, ws_dir):
            if d and os.path.isdir(d):
                for path in sorted(glob.glob(os.path.join(d, "*.json"))):
                    if os.path.basename(path).startswith("_"):
                        continue
                    t = _read(path)
                    t["_path"] = path
                    self.templates[t["id"]] = t  # the workspace overrides a shipped template with the same id
        stats_path = os.path.join(ws_dir, STATS_FILE) if ws_dir else None
        self.stats = _read(stats_path) if stats_path and os.path.exists(stats_path) else {}

    def get(self, tid):
        return self.templates.get(tid)

    def match(self, url, source=""):
        """Best template for a page: a learned site template for the exact host, else an ATS by host, else an ATS
        whose marker is in the page source (career sites on company domains that embed an ATS)."""
        t = self.templates.get("site:" + host_of(url)) or self.on_ats_host(url)
        if t:
            return t
        low = (source or "")[:400000].lower()
        for t in self.templates.values():
            if t.get("kind") == "ats" and any(m in low for m in t.get("match", {}).get("markers", [])):
                return t
        return None

    def patterns(self, tid, key):
        """A template's texts as one case-insensitive regex (anchored for button texts), or None."""
        t = self.templates.get(tid) or {}
        value = t.get(key)
        if not value:
            return None
        values = value if isinstance(value, list) else [value]
        if key == "confirmation":
            return re.compile("|".join(values), re.I)
        return re.compile(r"^\s*(" + "|".join(re.escape(v) for v in values) + r")\s*$", re.I)

    # ---------- learning ----------

    def _bump(self, tid, **counts):
        s = self.stats.setdefault(tid, {})
        for k, v in counts.items():
            s[k] = s.get(k, 0) + v
        s["last_seen"] = dt.datetime.now().isoformat(timespec="seconds")
        return s

    def _save(self, t):
        if not self.ws_dir:
            return
        if t.get("kind") == "site":
            t.pop("_path", None)
            _write(_file_for(self.ws_dir, t["id"]), t)
        _write(os.path.join(self.ws_dir, STATS_FILE), self.stats)

    def on_ats_host(self, url):
        host = host_of(url)
        return next((t for t in self.templates.values()
                     if t.get("kind") == "ats" and host_matches(host, t.get("match", {}).get("hosts", []))), None)

    def learn_extract(self, url, form, found):
        """After extract. `found`: what worked on this page (description_selector, apply_text, guest_method,
        submit_text, captcha, login, ats). → the template id written into form.json.
        A job on the ATS's own domain (jobs.ashbyhq.com) only counts towards the ATS template; a job on a company
        domain gets its own site template, even when the page embeds a known ATS (recorded as embedded_ats)."""
        t = self.on_ats_host(url) or self.on_ats_host(form.get("form_url", ""))
        if t is None:
            host = host_of(url)
            t = self.templates.get("site:" + host)
            if t is None:
                t = {"id": "site:" + host, "kind": "site", "name": host, "match": {"hosts": [host]},
                     "learned": dt.date.today().isoformat(), "verified": False, "notes": []}
                self.templates[t["id"]] = t
        if t.get("kind") == "site":
            for k in KEYS:
                if found.get(k) and not (k == "description_selector" and found[k] == "body"):
                    t[k] = found[k]
            for k in ("captcha", "login"):
                if found.get(k) is not None:
                    t[k] = found[k]
            t["fields"] = [{"label": (f.get("label") or f.get("name") or "")[:120], "type": f["type"],
                            "required": bool(f.get("required"))} for f in form.get("fields", [])]
            t["form_url_pattern"] = re.sub(r"\d{3,}", "<n>", urlparse(form.get("form_url", "")).path)
            t["description_found"] = bool(form.get("description_found"))
            if found.get("ats"):
                t["embedded_ats"] = found["ats"]
            t.setdefault("stats", {})
            for k, v in self._bump(t["id"], extracts=1).items():
                t["stats"][k] = v
        else:
            self._bump(t["id"], extracts=1)
        self._save(t)
        return t["id"]

    def learn_fill(self, tid, status, submit_label=None, confirmation=None, captcha=None, login=None):
        """After fill: the result on this site (submitted / needs_human ...), the submit button and the
        confirmation phrase that worked."""
        t = self.templates.get(tid)
        if not t:
            return
        counts = {"fills": 1}
        if status in ("submitted", "already_applied"):
            counts["submitted"] = 1
        elif status in ("needs_human", "validation_error"):
            counts["needs_human"] = 1
        s = self._bump(tid, **counts)
        if t.get("kind") == "site":
            t.setdefault("stats", {}).update(s)
            if submit_label and status in ("submitted", "already_applied"):
                t["submit_text"] = submit_label
            if confirmation:
                phrases = t.setdefault("confirmation", [])
                rx = re.escape(confirmation.strip()[:80])
                if rx and rx not in phrases:
                    phrases.append(rx)
            if status in ("submitted", "already_applied"):
                t["verified"] = True
            for k, v in (("captcha", captcha), ("login", login)):
                if v is not None:
                    t[k] = v
        self._save(t)


def library_for(ws):
    return Library(ws.path("site_templates") if ws else None)


# ---------- CLI ----------

def peviitor_random(n, timeout=20):
    """n random jobs from the peviitor.ro public API (read-only GET /v1/random/) → [{title, company, url, workmode}]."""
    import urllib.request
    out = []
    for _ in range(n):
        try:
            req = urllib.request.Request("https://api.peviitor.ro/v1/random/", headers={"User-Agent": "jobpilot/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.load(r)
        except Exception as e:
            print(f"WARN peviitor: {e!r}"[:120])
            continue
        url = j.get("url")
        url = url[0] if isinstance(url, list) else url
        if url:
            out.append({"title": j.get("title"), "company": j.get("company"), "url": url, "workmode": j.get("workmode")})
    return out


def main(argv=None, ws=None):
    from .workspace import Workspace, add_argument as add_workspace_argument
    ap = argparse.ArgumentParser(prog="jobpilot sites", description=__doc__.split("\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    add_workspace_argument(common)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", parents=[common])
    p = sub.add_parser("show", parents=[common])
    p.add_argument("id")
    p = sub.add_parser("match", parents=[common])
    p.add_argument("url")
    p = sub.add_parser("probe", parents=[common])
    p.add_argument("urls", nargs="*")
    p.add_argument("--peviitor", type=int, default=0, help="sample N random peviitor.ro jobs")
    p.add_argument("--out", help="folder for the probe extracts (default: a temporary folder)")
    a = ap.parse_args(argv)
    ws = ws or Workspace(a.workspace)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    lib = library_for(ws)

    if a.cmd == "list":
        for t in sorted(lib.templates.values(), key=lambda t: (t.get("kind") != "ats", t["id"])):
            s = {**lib.stats.get(t["id"], {}), **t.get("stats", {})}
            flags = ",".join(x for x, on in (("login", t.get("login") == "required"), ("captcha", t.get("captcha")),
                                              ("verified", t.get("verified"))) if on)
            print(f"{t['id']:<40} {t.get('kind', ''):<5} extracts {s.get('extracts', 0):>3}  fills {s.get('fills', 0):>3}  "
                  f"submitted {s.get('submitted', 0):>3}  {flags}")
        print(f"{len(lib.templates)} template(s)")
    elif a.cmd == "show":
        t = lib.get(a.id) or sys.exit(f"ERROR no template '{a.id}'")
        print(json.dumps({k: v for k, v in t.items() if k != "_path"}, ensure_ascii=False, indent=2))
        if a.id in lib.stats:
            print("stats:", json.dumps(lib.stats[a.id]))
    elif a.cmd == "match":
        t = lib.match(a.url)
        print(t["id"] if t else f"no template (a new site: {site_id(a.url)} is learned at the first extract)")
    elif a.cmd == "probe":
        return probe(ws, lib, a)
    return 0


def probe(ws, lib, a):
    import subprocess
    import tempfile
    jobs = [{"url": u, "company": "", "title": ""} for u in a.urls]
    if a.peviitor:
        sampled = peviitor_random(a.peviitor)
        agg = [j for j in sampled if is_aggregator(j["url"])]
        jobs += [j for j in sampled if not is_aggregator(j["url"])]
        print(f"peviitor: {len(sampled)} sampled, {len(agg)} on job boards/aggregators (skipped), "
              f"{len(sampled) - len(agg)} on company sites / ATS")
    out_root = a.out or tempfile.mkdtemp(prefix="jobpilot_probe_")
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    for i, j in enumerate(jobs, 1):
        before = lib.match(j["url"])
        out = os.path.join(out_root, f"{i:02d}_{re.sub(r'[^a-z0-9]+', '_', host_of(j['url']))[:40]}")
        cmd = [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv.pop(1)); from jobpilot import page; "
               "sys.exit(page.main())", pkg_parent, "extract", j["url"], "--out", out, "--workspace", ws.root]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            text = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            text = "timeout"
        form = _read(os.path.join(out, "form.json")) if os.path.exists(os.path.join(out, "form.json")) else None
        lib = library_for(ws)  # extract may have learned a template
        row = {"url": j["url"], "before": before["id"] if before else "-", "after": (form or {}).get("template", "-"),
               "description": bool(form and form.get("description_found")),
               "fields": len(form["fields"]) if form else 0,
               "cv": bool(form and any(f["type"] == "file" for f in form["fields"])),
               "submit": bool(form and form.get("submit_label")),
               "captcha": bool(form and any("CAPTCHA" in n for n in form.get("notes", []))),
               "login": bool(form and any("login" in n.lower() for n in form.get("notes", []))),
               "error": "" if form else (text.strip().splitlines() or ["?"])[-1][:100]}
        rows.append(row)
        print(f"{i:>2}. {host_of(j['url'])[:34]:<34} before {row['before'][:22]:<22} after {row['after'][:26]:<26} "
              f"desc {'Y' if row['description'] else '-'}  fields {row['fields']:>2}  cv {'Y' if row['cv'] else '-'}  "
              f"submit {'Y' if row['submit'] else '-'}  captcha {'Y' if row['captcha'] else '-'}  "
              f"login {'Y' if row['login'] else '-'}  {row['error']}")
    ok = [r for r in rows if not r["error"]]
    applyable = [r for r in ok if r["cv"] and r["submit"] and not r["captcha"] and not r["login"]]
    print(f"\nPROBED {len(rows)}  extracted {len(ok)}  with a description {sum(r['description'] for r in ok)}  "
          f"form with CV upload + submit, no CAPTCHA/login (auto-fillable) {len(applyable)}  out: {out_root}")
    return 0
