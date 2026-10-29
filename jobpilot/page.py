"""Read a job page (description + application form), fill the form and optionally submit it.

Usage:
    python -m jobpilot page extract <url> --out <dir>
        → <dir>/job.md (title, company, location, description) and <dir>/form.json (form fields)
    python -m jobpilot page fill --form <dir>/form.json --answers <answers.json> [--cv <file.pdf>] [--out <dir>]
                            [--submit] [--pool ANSWERS_POOL.json] [--applied-log <csv>] [--headless]
        → opens a Chromium window, attaches the CV first, fills the fields, reads every value back and
          re-fills what the page reset, answers fields that appear only after others were filled
          (chained dropdowns) from the pool, saves <dir>/prefill.png and <dir>/fill_report.txt.
          Without --submit: waits until the user closes the window (the user clicks Submit).
          With --submit: clicks Submit only when every required field is filled and verified and there is
          no login/CAPTCHA; reads the result page and logs it in the applied log. Anything else →
          STATUS needs_human and the window stays open for the user.
        STATUS line: prefilled | submitted | already_applied | validation_error | needs_human | skipped_already_applied

answers.json:  {"answers": {"<field_id>": <value>, ...}}
    text/email/tel/url/number/date/textarea → string · select/radio/combobox → option label
    checkbox → true/false (single) or a list of option labels (group) · file → "CV"
    null or missing → left empty for the user.

LinkedIn URLs are refused on purpose: the user's LinkedIn account must never be involved.
Requires: pip install playwright && python -m playwright install chromium
"""
import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

from . import answers as match_answers
from . import sites
from .workspace import Workspace, add_argument as add_workspace_argument

BLOCKED_DOMAINS = ("linkedin.com", "lnkd.in")
ATS = {
    "greenhouse.io": "Greenhouse", "lever.co": "Lever", "workable.com": "Workable",
    "smartrecruiters.com": "SmartRecruiters", "recruitee.com": "Recruitee", "teamtailor.com": "Teamtailor",
    "bamboohr.com": "BambooHR", "personio": "Personio", "ashbyhq.com": "Ashby", "myworkdayjobs.com": "Workday",
    "workday.com": "Workday", "taleo.net": "Taleo", "successfactors": "SuccessFactors", "icims.com": "iCIMS",
    "jobvite.com": "Jobvite", "breezy.hr": "Breezy", "ejobs.ro": "eJobs", "bestjobs.eu": "BestJobs",
    "oraclecloud.com": "Oracle Recruiting",
}
ORACLE_JOB = re.compile(r"https://([^/]+\.oraclecloud\.com)/hcmUI/CandidateExperience/([^/]+)/sites/([^/]+)/job/(\d+)", re.I)
LOGIN_ATS = ("Workday", "Taleo", "SuccessFactors", "iCIMS", "Oracle Recruiting")
DESCRIPTION_SELECTORS = [
    ".job__description", "#content .job-post", "#app_body", "#content",            # Greenhouse
    "[data-qa='job-description']", ".posting-page .section-wrapper", ".posting",    # Lever
    "[data-ui='job-description']", "[data-ui='job-details']",                      # Workable
    ".job-sections", "[itemprop='description']",                                   # SmartRecruiters
    ".job-description", ".description", "#job-description",                        # Recruitee / generic
    "main", "article", "[role='main']",
]
APPLY_TEXT = re.compile(r"^\s*(apply( now| for (this )?(job|position|role))?|aplic[ăa]( acum)?|candideaz[ăa]|trimite cv)\s*$", re.I)
CONFIRMATION_TEXT = re.compile(r"thank(s| you) for (applying|your application|submitting)|"
                               r"application (was |has been )?(successfully )?(received|submitted)|successfully submitted|"
                               r"mul[țt]umim pentru (aplicare|candidatur)|candidatura (a fost|dvs)", re.I)
ALREADY_TEXT = re.compile(r"already applied|you have applied|ai aplicat deja|alreadyapplied", re.I)
ERROR_TEXT = re.compile(r"please (enter|select|fill|complete|provide)|is required|required field|field is required|"
                        r"c[âa]mp(ul)? (este )?obligatoriu|invalid", re.I)
SUBMIT_TEXT = re.compile(r"^\s*(submit( (my |your )?application)?|send( (my |your )?application)?|apply( now)?|"
                         r"complete (my |your )?application|trimite( (aplica[țt]ia|candidatura|cv-ul))?|aplic[ăa]|"
                         r"finalizeaz[ăa]( aplicarea)?)\s*$", re.I)
PAGE_ATS = {"phenompeople": "Phenom", "greenhouse.io": "Greenhouse", "lever.co": "Lever", "smartrecruiters": "SmartRecruiters",
            "myworkdayjobs": "Workday", "successfactors": "SuccessFactors", "workable.com": "Workable"}
# Login pages that also let you apply without an account (Avature: "Upload File" / "Enter Data").
GUEST_METHOD = re.compile(r"^\s*(upload (file|(your |my )?(cv|resume))|apply (without (an )?account|as (a )?guest)|"
                          r"continue as (a )?guest|[îi]ncarc[ăa] (cv(-ul)?|fi[șs]ier)|aplic[ăa] f[ăa]r[ăa] cont)\s*$", re.I)
COOKIE_DECLINE = re.compile(r"^\s*(accept only (strictly )?necessary( cookies)?|only (strictly )?necessary( cookies)?|"
                            r"reject( all)?( cookies)?|decline( all)?( cookies)?|necessary cookies only|use necessary cookies only|"
                            r"refuz[ăa]( tot| toate)?|respinge( tot| toate)?|doar (cookie-urile )?(strict )?necesare)\s*$", re.I)
NEXT_TEXT = re.compile(r"^\s*(continue|next|continu[ăa]|urm[ăa]torul|pasul urm[ăa]tor)\s*$", re.I)
MIN_DESCRIPTION_CHARS = 600  # below this the page is treated as form-only: ask the user for the description
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

SCAN_JS = r"""
() => {
  const shown = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  // File inputs are often hidden behind a styled button: then the surrounding element must be visible.
  // Radios/checkboxes are often hidden behind a styled label (Workable): then the label must be visible.
  const visible = (el) => shown(el) || (el.type === 'file' && (shown(el.parentElement) || shown(el.closest('label, div, form')))) ||
    ((el.type === 'radio' || el.type === 'checkbox') && shown(el.closest('label')));
  const clean = (t) => (t || '').replace(/\s+/g, ' ').replace(/\s*\*\s*$/, '').trim().slice(0, 300);
  const GENERIC = /^(attach|upload|choose file|browse|select file|încarcă|incarca|alege fi[șs]ier|drag.*drop.*)$/i;
  const isQuestion = (l, el) => {
    const t = clean(l.innerText);
    return t && !GENERIC.test(t) && !l.contains(el) && !l.querySelector('input, select, textarea');
  };
  const byLabelledby = (node) => {
    const ids = node && node.getAttribute('aria-labelledby');
    if (!ids) return '';
    return clean(ids.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText).join(' '));
  };
  const fileLabel = (el) => {
    const grp = el.closest('[aria-labelledby]');
    if (grp && byLabelledby(grp) && !GENERIC.test(byLabelledby(grp))) return byLabelledby(grp);
    let n = el.parentElement;
    for (let i = 0; n && i < 3; i++, n = n.parentElement) {
      for (const l of n.querySelectorAll('[id*="label"], [class*="label"], label, legend')) if (isQuestion(l, el)) return clean(l.innerText);
    }
    const hint = (el.name || el.id || '').toLowerCase();
    if (/resume|cv/.test(hint)) return 'Resume/CV';
    if (/cover/.test(hint)) return 'Cover letter';
    return '';
  };
  const labelOf = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && clean(l.innerText) && !GENERIC.test(clean(l.innerText))) return clean(l.innerText);
    }
    if (el.type === 'file') { const t = fileLabel(el); if (t) return t; }
    const lab = el.getAttribute('aria-labelledby');
    if (lab) {
      const t = lab.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText).join(' ');
      if (clean(t)) return clean(t);
    }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    const wrap = el.closest('label');
    if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
    const box = el.closest('fieldset, .field, .form-group, .application-question, [class*="field"], [class*="question"], li, div');
    if (box) {
      const l = box.querySelector('label, legend, .label, [class*="label"]');
      if (l && clean(l.innerText)) return clean(l.innerText);
    }
    // Question title a few levels up (Ashby autocomplete: input > div > div > field entry > label)
    for (let n = el.parentElement, i = 0; n && i < 5; n = n.parentElement, i++) {
      const l = [...n.querySelectorAll(':scope > label, :scope > legend')].find(x => isQuestion(x, el));
      if (l) return clean(l.innerText);
    }
    return clean(el.getAttribute('placeholder') || el.name || el.id);
  };
  // Required marked only by the question title's class (Ashby "_required_")
  const titleRequired = (el) => {
    const box = el.closest('fieldset, [class*="fieldEntry"], [class*="field-entry"]');
    const t = box && box.querySelector(':scope > label, :scope > legend');
    return !!(t && /required/i.test(String(t.className)));
  };
  // Help text under the question ("Gross annual base salary, local currency"): used by the matcher's variants
  const hintOf = (el) => {
    const box = el.closest('fieldset, [class*="fieldEntry"], [class*="field-entry"], .application-question, .field, .form-group');
    const d = box && box.querySelector('[class*="description"], [class*="help"], [class*="hint"]');
    return d && !d.contains(el) ? clean(d.innerText) : '';
  };
  const groupLabel = (el) => {
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector('legend'); if (lg && clean(lg.innerText)) return clean(lg.innerText);
      const t = fs.querySelector(':scope > label'); if (t && isQuestion(t, el)) return clean(t.innerText);  // Ashby: title label, no legend
    }
    const grp = el.closest('[role="group"], [role="radiogroup"]');
    if (byLabelledby(grp)) return byLabelledby(grp);
    for (const q of ['.application-question, [class*="question"]', '.field, .form-group, li.form-field']) {
      const box = el.closest(q);
      if (!box) continue;
      for (const l of box.querySelectorAll('[class*="label"], legend, label, .text')) if (isQuestion(l, el)) return clean(l.innerText);
    }
    return clean(el.name);
  };
  const sel = (el) => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const tag = el.tagName.toLowerCase();
    if (el.name) {
      let s = `${tag}[name="${CSS.escape(el.name)}"]`;
      if ((el.type === 'radio' || el.type === 'checkbox') && el.value) s += `[value="${CSS.escape(el.value)}"]`;
      if (document.querySelectorAll(s).length === 1) return s;
    }
    const path = [];
    let n = el;
    while (n && n.nodeType === 1 && path.length < 8) {
      let i = 1, p = n;
      while ((p = p.previousElementSibling)) if (p.tagName === n.tagName) i++;
      path.unshift(`${n.tagName.toLowerCase()}:nth-of-type(${i})`);
      if (n.id) { path[0] = '#' + CSS.escape(n.id); break; }
      n = n.parentElement;
    }
    return path.join(' > ');
  };
  const fields = [], groups = {}, fsIds = new Map();
  const skip = new Set(['hidden', 'submit', 'button', 'reset', 'image', 'search', 'password']);
  for (const el of document.querySelectorAll('input, select, textarea, [role="combobox"]')) {
    const type = el.getAttribute('role') === 'combobox' && el.tagName !== 'SELECT' && el.type !== 'text' && el.tagName !== 'INPUT'
      ? 'combobox' : (el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text').toLowerCase());
    if (skip.has(type) || !visible(el) || el.disabled) continue;
    const styledChoice = (type === 'radio' || type === 'checkbox') && shown(el.closest('label'));
    if (type !== 'file' && !styledChoice && (el.getAttribute('aria-hidden') === 'true' || el.classList.contains('requiredInput'))) continue;
    const required = el.required || el.getAttribute('aria-required') === 'true' || titleRequired(el);
    if (type === 'radio' || type === 'checkbox') {
      // Checkboxes with different names inside one fieldset are one multi-select question (Ashby)
      const fs = type === 'checkbox' && el.closest('fieldset');
      const fsKey = fs && fs.querySelectorAll('input[type="checkbox"]').length > 1
        ? 'fs' + (fsIds.get(fs) ?? fsIds.set(fs, fsIds.size).get(fs)) : '';
      const key = type + ':' + (fsKey || el.name || sel(el));
      const g = groups[key] || (groups[key] = { type, name: el.name || '', label: groupLabel(el), required: false, options: [] });
      g.required = g.required || required;
      g.options.push({ label: labelOf(el), value: el.value, selector: sel(el) });
      continue;
    }
    const f = { type, selector: sel(el), name: el.name || el.id || '', label: labelOf(el), required };
    if (el.form && el.form.querySelector('input[type="password"]')) f.in_login = true;
    if (type === 'select') f.options = [...el.options].map(o => clean(o.text)).filter(Boolean);
    if (el.maxLength > 0) f.maxlength = el.maxLength;
    if (type === 'file') f.accept = el.accept || '';
    if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete')) f.type = type === 'select' ? 'select' : 'combobox';
    const h = hintOf(el); if (h) f.hint = h;
    fields.push(f);
  }
  for (const g of Object.values(groups)) {
    if (g.type === 'checkbox' && g.options.length === 1) {
      fields.push({ type: 'checkbox', selector: g.options[0].selector, name: g.name, label: g.options[0].label || g.label, required: g.required });
    } else fields.push(g);
  }
  // Yes/No answered with buttons over a hidden checkbox (Ashby): a choice of type "buttons"
  for (const box of document.querySelectorAll('[class*="yesno"]')) {
    const buttons = [...box.querySelectorAll('button')].filter(shown);
    const entry = box.closest('[data-field-path]');
    if (buttons.length < 2 || !entry) continue;
    const title = entry.querySelector(':scope > label, :scope > legend');
    const path = `[data-field-path="${CSS.escape(entry.getAttribute('data-field-path'))}"]`;
    fields.push({
      type: 'buttons', name: entry.getAttribute('data-field-path'), label: clean(title && title.innerText),
      required: titleRequired(box),
      options: buttons.map((b, i) => ({ label: clean(b.innerText), value: b.getAttribute('data-option') || '',
        selector: b.getAttribute('data-option') ? `${path} button[data-option="${CSS.escape(b.getAttribute('data-option'))}"]`
                                                : `${path} button:nth-of-type(${i + 1})` })),
    });
  }
  const has = (q) => !!document.querySelector(q);
  return {
    fields,
    password: has('input[type="password"]'),
    captcha: has('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, .cf-turnstile'),
    next_button: [...document.querySelectorAll('button, a')].some(b => /^\s*(next|continue|următorul|continuă|pasul următor)\s*$/i.test(b.innerText || '')),
  };
}
"""


def blocked(url):
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def ats_of(url):
    host = urlparse(url).netloc.lower()
    return next((name for key, name in ATS.items() if key in host), "")


def clean_url(url):
    """Drop tracking parameters (_gl, utm_*, gclid…) that change on every visit."""
    from urllib.parse import parse_qsl, urlencode, urlunparse
    u = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
         if not re.match(r"^(_gl|_ga|utm_|gclid|fbclid|mc_|ref$|src$|source$)", k, re.I)]
    return urlunparse(u._replace(query=urlencode(q)))


def ats_of_page(page):
    """Career sites on a company domain (e.g. jobs-cee.pwc.com) give their ATS away in the page source."""
    try:
        src = page.content()[:400000].lower()
    except Exception:
        return ""
    return next((name for key, name in PAGE_ATS.items() if key in src), "")


def text_of(fragment):
    t = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", fragment or "", flags=re.I)
    t = re.sub(r"<li[^>]*>", "- ", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return "\n".join(l.strip() for l in t.splitlines() if l.strip())


def job_posting(page):
    for raw in page.eval_on_selector_all('script[type="application/ld+json"]', "els => els.map(e => e.textContent)"):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if not isinstance(d, dict):
                continue
            stack.extend(d.get("@graph", []) if isinstance(d.get("@graph"), list) else [])
            t = d.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                org = d.get("hiringOrganization") or {}
                locs = d.get("jobLocation") or []
                locs = locs if isinstance(locs, list) else [locs]
                places = []
                for loc in locs:
                    addr = (loc or {}).get("address") or {}
                    if isinstance(addr, dict):
                        places.append(", ".join(x for x in (addr.get("addressLocality"), addr.get("addressCountry")
                                                            if isinstance(addr.get("addressCountry"), str) else "") if x))
                if d.get("jobLocationType") == "TELECOMMUTE":
                    places.append("Remote")
                return {"title": d.get("title", ""), "company": org.get("name", "") if isinstance(org, dict) else str(org),
                        "location": "; ".join(p for p in places if p), "posted": d.get("datePosted", ""),
                        "employment_type": d.get("employmentType", ""), "description": text_of(d.get("description", ""))}
    return None


def page_description(page, first=None):
    """→ (text, selector that gave it). `first`: a site template's selector, tried before the generic list."""
    for frame in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        for selector in ([first] if first else []) + DESCRIPTION_SELECTORS:
            try:
                loc = frame.locator(selector).first
                if loc.count():
                    txt = loc.inner_text(timeout=2000).strip()
                    if len(txt) > 400:
                        return txt, selector
            except Exception:
                continue
    return page.locator("body").inner_text().strip(), "body"


def confirmation_line(text, extra=None):
    """The line of the result page that confirms the application (learned into the site template), or ''."""
    for line in (text or "").splitlines():
        if CONFIRMATION_TEXT.search(line) or (extra and extra.search(line)):
            return line.strip()[:120]
    return ""


REQUIRED_MARK = re.compile(r"\s*(\(\s*(required|obligatoriu)\s*\)|\b(required|obligatoriu)\b)\s*$", re.I)
# Buttons of other forms on the page (login, newsletter, search) are never the application's submit button.
NOT_SUBMIT = re.compile(r"log ?in|sign ?in|sign ?up|autentific|conecteaz|subscribe|abonea|newsletter|search|caut|"
                        r"linkedin|indeed|google|facebook|filt", re.I)


def required_from_label(label):
    """Sites that mark required fields only in the label text ("First name* Required", "Email *", "Nume (obligatoriu)")
    → (label without the trailing marker, required)."""
    text = (label or "").strip()
    cleaned = REQUIRED_MARK.sub("", text).strip()
    return cleaned, cleaned != text or cleaned.endswith("*")


def scan_all(page):
    fields, flags = [], {"password": False, "captcha": False, "next_button": False}
    for i, frame in enumerate(page.frames):
        try:
            res = frame.evaluate(SCAN_JS)
        except Exception:
            continue
        for f in res["fields"]:
            f["frame_url"] = "" if frame == page.main_frame else frame.url
            f["label"], marked = required_from_label(f.get("label"))
            f["required"] = bool(f.get("required")) or marked
            fields.append(f)
        for k in flags:
            flags[k] = flags[k] or res[k]
    for n, f in enumerate(fields, 1):
        base = re.sub(r"[^a-z0-9]+", "_", (f.get("label") or f.get("name") or f["type"]).lower()).strip("_")[:40]
        f["field_id"] = f"f{n:02d}_{base or f['type']}"
    return fields, flags


# A field that does not respond within ACTION_MS is reported as ERROR instead of stalling the run for 30 s.
ACTION_MS = 10000
SETTLE_MS = 8000


MAX_COMBO_OPTIONS = 80  # longer lists are search-driven (locations, schools): left without options


def combobox_options(page, fields):
    """Typeahead comboboxes (Ashby "Start typing...") render their options only while open: open each one,
    read the visible options, close it. A list that stays empty or is too long is search-driven."""
    for f in fields:
        if f["type"] != "combobox" or f.get("options"):
            continue
        frame = frame_for(page, f.get("frame_url"))
        if frame is None:
            continue
        try:
            loc = frame.locator(f["selector"]).first
            loc.scroll_into_view_if_needed(timeout=3000)
            read = """() => [...document.querySelectorAll('[role="option"]')]
                .filter(e => e.getClientRects().length).map(e => e.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean)"""
            loc.click(timeout=3000)
            frame.wait_for_timeout(600)
            opts = frame.evaluate(read)
            if not opts:  # Ashby opens the list on ArrowDown, not on click
                loc.press("ArrowDown")
                frame.wait_for_timeout(600)
                opts = frame.evaluate(read)
            loc.press("Escape")
        except Exception as e:
            print(f"WARN options of '{(f.get('label') or '')[:50]}': {str(e).splitlines()[0][:80]}")
            continue
        opts = list(dict.fromkeys(opts))
        if 0 < len(opts) <= MAX_COMBO_OPTIONS:
            f["options"] = opts
        else:
            f["options_search"] = True


def wait_settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except Exception:
        pass


def decline_cookies(page):
    """Cookie banners cover the page and swallow clicks: answer them with the necessary-only / reject option."""
    for role in ("button", "link"):
        loc = page.get_by_role(role, name=COOKIE_DECLINE)
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    loc.nth(i).click(timeout=3000)
                    page.wait_for_timeout(800)
                    return True
            except Exception:
                continue
    return False


def click_visible(page, pattern):
    """Click the first visible link/button whose name matches; return its text or ''."""
    decline_cookies(page)
    for role in ("link", "button"):
        loc = page.get_by_role(role, name=pattern)
        for i in range(loc.count()):
            b = loc.nth(i)
            try:
                if b.is_visible():
                    text = b.inner_text().strip()
                    b.click(timeout=5000)
                    wait_settle(page)
                    page.wait_for_timeout(1000)
                    return text
            except Exception:
                continue
    return ""


def oracle_job(url):
    """Oracle Recruiting Cloud renders the ad with JavaScript; its public REST API returns the same data."""
    m = ORACLE_JOB.match(url)
    if not m:
        return None
    import urllib.request
    host, lang, site, job_id = m.groups()
    api = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
           f"&finder=ById;Id=%22{job_id}%22,siteNumber={site}")
    req = urllib.request.Request(api, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        item = (json.load(resp).get("items") or [{}])[0]
    if not item.get("Title"):
        return None
    parts = [("", item.get("ExternalDescriptionStr")), ("Responsibilities", item.get("ExternalResponsibilitiesStr")),
             ("Qualifications", item.get("ExternalQualificationsStr")), ("About the company", item.get("CorporateDescriptionStr"))]
    desc = "\n\n".join((f"## {h}\n" if h else "") + text_of(v) for h, v in parts if v)
    company = ""  # not in the API; the description usually names the employer
    return {"title": item.get("Title", ""), "company": company, "location": item.get("PrimaryLocation") or "",
            "posted": (item.get("ExternalPostedStartDate") or "")[:10], "employment_type": item.get("JobSchedule") or "",
            "description": desc, "apply_url": f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{job_id}/apply/email"}


def cmd_extract(a):
    if blocked(a.url):
        sys.exit("REFUSED: LinkedIn links are not opened. Give the job link from the company site / ATS, "
                 "or paste the job description.")
    oracle = None
    try:
        oracle = oracle_job(a.url)
    except Exception as e:
        print("WARN Oracle API:", str(e)[:120])
    if oracle:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "job.md"), "w", encoding="utf-8") as f:
            f.write(f"# {oracle['title']} - {oracle['company']}\n\nLink: {a.url}\n")
            f.write(f"Location: {oracle['location']} | Type: {oracle['employment_type']} | Posted: {oracle['posted']}\n\n")
            f.write(oracle["description"] + "\n")
        notes = ["Oracle Recruiting: the application starts with your email and a one-time code sent to it; "
                 "the user enters both in the browser, then the form pages can be filled step by step"]
        form = {"job_url": a.url, "form_url": oracle["apply_url"], "apply_click": "", "ats": "Oracle Recruiting",
                "description_found": len(oracle["description"]) >= MIN_DESCRIPTION_CHARS,
                "notes": notes, "extracted_at": dt.datetime.now().isoformat(timespec="seconds"), "fields": []}
        with open(os.path.join(a.out, "form.json"), "w", encoding="utf-8") as f:
            json.dump(form, f, ensure_ascii=False, indent=2)
        print(f"JOB {oracle['title']} | {oracle['company'] or '?'} | {oracle['location']} | {len(oracle['description'])} chars")
        print(f"FORM 0 fields | ATS: Oracle Recruiting | form_url: {oracle['apply_url']}")
        for n in notes:
            print("NOTE", n)
        print("saved", os.path.join(a.out, "job.md"), os.path.join(a.out, "form.json"))
        return
    from playwright.sync_api import sync_playwright
    os.makedirs(a.out, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA, locale="en-US")
        page.set_default_timeout(ACTION_MS)
        page.goto(a.url, wait_until="domcontentloaded", timeout=45000)
        wait_settle(page)
        lib = sites.library_for(a.ws)
        try:
            tpl = lib.match(a.url, page.content())
        except Exception:
            tpl = lib.match(a.url)
        tid = tpl["id"] if tpl else None
        if tpl:
            print(f"TEMPLATE {tid} (known)")
        found = {}
        info = job_posting(page) or {}
        if info.get("description"):
            found["description_selector"] = "ld+json"
        else:
            info["description"], found["description_selector"] = page_description(
                page, (tpl or {}).get("description_selector") if (tpl or {}).get("description_selector") != "ld+json" else None)
        info.setdefault("title", (page.locator("h1").first.inner_text() if page.locator("h1").count() else page.title()).strip())
        if not info.get("company"):
            og = page.locator('meta[property="og:site_name"]')
            info["company"] = og.first.get_attribute("content") if og.count() else ""
        if not info.get("company") and ats_of(page.url) in ("Greenhouse", "Lever", "Ashby", "Workable"):
            parts = [x for x in urlparse(page.url).path.split("/") if x]
            info["company"] = parts[0].replace("-", " ").title() if parts else ""
        job_url = page.url
        fields, flags = scan_all(page)
        apply_click = ""
        # Job pages often carry unrelated inputs (job alerts, "save job"); without a CV upload it is not the form.
        if not any(f["type"] == "file" for f in fields):
            page_fields, page_flags = fields, flags
            apply_patterns = [p for p in (lib.patterns(tid, "apply_text"), APPLY_TEXT) if p]
            for role, pattern in [(r, p) for p in apply_patterns for r in ("link", "button")]:
                target = page.get_by_role(role, name=pattern)
                if target.count():
                    apply_click = target.first.inner_text().strip()
                    href = target.first.get_attribute("href") or ""
                    try:
                        with page.context.expect_page(timeout=4000) as new:
                            target.first.click(timeout=5000)
                        page = new.value
                    except Exception:
                        # Sticky headers can cover the button (Phenom); the link target is enough.
                        if href.startswith("http") and page.url.split("#")[0] == job_url.split("#")[0]:
                            page.goto(href, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_load_state("domcontentloaded")
                    wait_settle(page)
                    fields, flags = scan_all(page)
                    break
            if not fields:
                fields, flags = page_fields, page_flags
        method_click = ""
        if flags["password"]:
            method_click = (click_visible(page, lib.patterns(tid, "guest_method")) if lib.patterns(tid, "guest_method")
                            else "") or click_visible(page, GUEST_METHOD)
            if method_click:
                fields, flags = scan_all(page)
                fields = [f for f in fields if not f.get("in_login")]
        form_url = page.url
        ats = ats_of(form_url) or ats_of(job_url) or ats_of_page(page)
        notes = []
        if blocked(form_url):
            fields, notes = [], ["the Apply button leads to LinkedIn - not opened; apply manually or find the company form"]
        if method_click:
            notes.append(f"no account needed: fill chooses '{method_click}', uploads the CV, clicks Continue and "
                         "answers the next step from the pool")
        elif ats in LOGIN_ATS or flags["password"] or (tpl or {}).get("login") == "required":
            notes.append("login/account required - the user creates or signs into the account; the script fills only the current page")
        if flags["captcha"]:
            notes.append("CAPTCHA present - the user solves it")
        if flags["next_button"]:
            notes.append("multi-step form - only the visible step is filled; run fill again on the next step")
        description_found = len(info["description"]) >= MIN_DESCRIPTION_CHARS
        if not description_found:
            notes.insert(0, "NO_DESCRIPTION: this page has no job description (form-only or not readable) - "
                            "ask the user to paste the job description; do not search for it elsewhere")
        if fields and not any(f["type"] == "file" for f in fields):
            notes.append("no file field found - the CV may need to be attached manually")
        combobox_options(page, fields)
        submit_label = ""
        if fields:
            try:
                _, submit_label = find_submit(page, lib.patterns(tid, "submit_text"))
            except Exception:
                pass
        found.update(apply_text=apply_click, guest_method=method_click, submit_text=submit_label, ats=ats,
                     captcha=bool(flags["captcha"]),
                     login="required" if any(n.startswith("login/account required") for n in notes) else
                           ("optional" if method_click else "none"))
        browser.close()
    with open(os.path.join(a.out, "job.md"), "w", encoding="utf-8") as f:
        f.write(f"# {info.get('title', '')} - {info.get('company', '')}\n\nLink: {clean_url(job_url)}\n")
        f.write(f"Location: {info.get('location', '')} | Type: {info.get('employment_type', '')} | Posted: {info.get('posted', '')}\n\n")
        f.write(info["description"] + "\n")
    form = {"job_url": clean_url(job_url), "form_url": clean_url(form_url), "apply_click": apply_click,
            "method_click": method_click, "ats": ats, "notes": notes,
            "description_found": description_found, "submit_label": submit_label,
            "extracted_at": dt.datetime.now().isoformat(timespec="seconds"), "fields": fields}
    try:
        form["template"] = lib.learn_extract(form["job_url"], form, found)
    except Exception as e:  # the library never blocks an application
        print(f"WARN site template not saved: {str(e)[:120]}")
    with open(os.path.join(a.out, "form.json"), "w", encoding="utf-8") as f:
        json.dump(form, f, ensure_ascii=False, indent=2)
    req = sum(1 for f in fields if f.get("required"))
    print(f"JOB {info.get('title', '')} | {info.get('company', '')} | {info.get('location', '')} | "
          f"{len(info['description'])} chars")
    print(f"FORM {len(fields)} fields ({req} required) | ATS: {ats or 'unknown'} | form_url: {form_url}")
    if form.get("template") and form["template"] != tid:
        print(f"TEMPLATE {form['template']} (learned from this page)")
    for n in notes:
        print("NOTE", n)
    print("saved", os.path.join(a.out, "job.md"), os.path.join(a.out, "form.json"))


def frame_for(page, frame_url):
    if not frame_url:
        return page.main_frame
    want = urlparse(frame_url)
    for fr in page.frames:
        got = urlparse(fr.url)
        if got.netloc == want.netloc and got.path == want.path:
            return fr
    return None


def norm(t):
    return re.sub(r"\s+", " ", str(t or "")).strip().casefold()


def pick_option(options, value):
    v = norm(value)
    for o in options:
        if norm(o["label"]) == v or norm(o.get("value")) == v:
            return o
    return next((o for o in options if v and v in norm(o["label"])), None)


def option_locator(frame, f, o):
    """The option's saved selector, else the option label inside its question (some ATS, e.g. Ashby,
    regenerate input ids on every page load)."""
    loc = frame.locator(o["selector"])
    if loc.count() or not o.get("label"):
        return loc.first
    scope = frame.locator("fieldset, [role=radiogroup], [role=group]").filter(has_text=f.get("label") or "")
    if not scope.count():
        scope = frame.locator("body")
    by_label = scope.first.get_by_label(o["label"], exact=True)
    return by_label.first if by_label.count() else loc.first


def single_checkbox(frame, f):
    """A lone checkbox by its saved selector, else by its label (ids regenerated on load, e.g. Ashby)."""
    loc = frame.locator(f["selector"])
    if loc.count() or not f.get("label"):
        return loc.first
    by_label = frame.get_by_label(f["label"], exact=True)
    return by_label.first if by_label.count() else loc.first


def fill_field(frame, f, value, cv):
    t = f["type"]
    if t == "buttons":
        o = pick_option(f["options"], value)
        if not o:
            raise ValueError(f"option not found: {value!r}")
        frame.locator(o["selector"]).first.click()
        return o["label"]
    if t in ("radio",) or (t == "checkbox" and "options" in f):
        values = value if isinstance(value, list) else [value]
        done = []
        for v in values:
            o = pick_option(f["options"], v)
            if not o:
                raise ValueError(f"option not found: {v!r}")
            option_locator(frame, f, o).check(force=True)
            done.append(o["label"])
        return ", ".join(done)
    loc = frame.locator(f["selector"]).first
    if t == "checkbox":
        loc = single_checkbox(frame, f)
    if t == "file":
        if not cv:
            raise ValueError("no --cv file given")
        loc.set_input_files(cv)
        return os.path.basename(cv)
    if t == "checkbox":
        loc.check(force=True) if value else loc.uncheck(force=True)
        return "checked" if value else "unchecked"
    if t == "select":
        try:
            loc.select_option(label=str(value))
        except Exception:
            match = next((o for o in f.get("options", []) if norm(value) in norm(o)), None)
            if not match:
                raise
            loc.select_option(label=match)
        return str(value)
    if t == "combobox":
        loc.click()
        if loc.evaluate("e => e.tagName === 'INPUT' || e.tagName === 'TEXTAREA'"):
            loc.fill(str(value))
        elif loc.evaluate("e => e.tagName === 'BUTTON'"):  # intl-tel-input flag button: the click focuses its search box
            frame.page.keyboard.type(str(value))
        else:
            loc.type(str(value))
        frame.wait_for_timeout(700)
        opt = frame.get_by_role("option", name=re.compile(re.escape(str(value)), re.I))
        if opt.count():
            opt.first.click()
        else:
            loc.press("Enter")
        return str(value)
    text = str(value)
    if f.get("maxlength") and len(text) > f["maxlength"]:
        raise ValueError(f"value longer than maxlength {f['maxlength']}")
    loc.fill(text)
    return text if len(text) <= 60 else text[:57] + "..."


def read_value(frame, f):
    t = f["type"]
    if t == "buttons":
        return [o["label"] for o in f["options"]
                if frame.locator(o["selector"]).first.get_attribute("aria-pressed") == "true"]
    if t == "radio" or (t == "checkbox" and "options" in f):
        return [o["label"] for o in f["options"] if option_locator(frame, f, o).is_checked()]
    loc = frame.locator(f["selector"]).first
    if t == "checkbox":
        return single_checkbox(frame, f).is_checked()
    if t == "select":
        return loc.evaluate("e => e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : ''")
    if t == "file":
        return None  # the ATS often replaces the input after parsing the CV; upload errors are caught at fill time
    try:
        return loc.input_value()
    except Exception:
        return loc.inner_text()


def same(expected, got, f):
    if f["type"] == "file" or got is None:
        return True
    if isinstance(got, bool):
        return got == bool(expected)
    if isinstance(got, list):
        want = expected if isinstance(expected, list) else [expected]
        return all(any(norm(w) == norm(g) or norm(w) in norm(g) for g in got) for w in want)
    e, g = norm(expected), norm(got)
    if e == g or (f["type"] in ("select", "combobox") and e and e in g):
        return True
    de, dg = re.sub(r"\D", "", e), re.sub(r"\D", "", g)
    return len(de) >= 6 and de == dg  # phone / date reformatted by the page


def field_key(f):
    if f.get("selector") is None or (f["type"] in ("radio", "buttons", "checkbox") and f.get("options") and f.get("label")):
        return (urlparse(f.get("frame_url", "")).path, f["type"], f.get("label", ""))  # option groups have no stable selector
    return (urlparse(f.get("frame_url", "")).path, f["selector"])


def find_submit(page, prefer=None):
    """→ (button, label). `prefer`: a site template's submit text, tried before the generic submit buttons."""
    for frame in page.frames:
        locs = ([frame.get_by_role("button", name=prefer)] if prefer else []) + [
            frame.locator("button[type=submit], input[type=submit]"), frame.get_by_role("button", name=SUBMIT_TEXT)]
        for loc in locs:
            for i in range(loc.count() - 1, -1, -1):
                b = loc.nth(i)
                try:
                    label = (b.inner_text() or b.get_attribute("value") or "").strip()
                    if b.is_visible() and b.is_enabled() and not NOT_SUBMIT.search(label):
                        return b, label
                except Exception:
                    continue
    return None, ""


def page_text(page):
    parts = [page.url]
    for frame in page.frames:
        try:
            parts.append(frame.locator("body").inner_text(timeout=3000)[:8000])
        except Exception:
            continue
    return "\n".join(parts)


def read_applied(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def log_applied(path, row):
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "application", "status", "job_url", "form_url"])
        if new:
            w.writeheader()
        w.writerow(row)


def cmd_fill(a):
    form = json.load(open(a.form, encoding="utf-8"))
    answers = json.load(open(a.answers, encoding="utf-8")).get("answers", {})
    if blocked(form["form_url"]):
        sys.exit("REFUSED: LinkedIn form")
    by_id = {f["field_id"]: f for f in form["fields"]}
    unknown = [k for k in answers if k not in by_id]
    if unknown:
        sys.exit(f"answers.json has unknown field ids: {unknown}")
    if a.cv and not os.path.exists(a.cv):
        sys.exit(f"CV file not found: {a.cv}")
    out = a.out or os.path.dirname(os.path.abspath(a.form))
    if a.check:
        missing = [f["field_id"] for f in form["fields"] if f.get("required") and answers.get(f["field_id"]) in (None, "", [])]
        print(f"answers for {len(answers)} of {len(by_id)} fields; required without answer: {missing or 'none'}")
        return
    applied_log = a.applied_log or a.ws.path("applied_log")
    urls = {clean_url(form["job_url"]), clean_url(form["form_url"])}
    done_before = [r for r in read_applied(applied_log) if r["status"] in ("submitted", "already_applied")
                   and (clean_url(r["job_url"]) in urls or clean_url(r["form_url"]) in urls)]
    if done_before and not a.force:
        print(f"STATUS skipped_already_applied ({done_before[-1]['date']}, {done_before[-1]['status']}) - use --force to run anyway")
        return
    pool = None
    if a.pool and os.path.exists(a.pool):
        pool = match_answers.load_pool(a.pool)
    lib = sites.library_for(a.ws)
    tid = form.get("template")
    if not lib.get(tid or ""):
        tpl = lib.match(form["job_url"]) or lib.match(form["form_url"])
        tid = tpl["id"] if tpl else None
    confirm_extra = lib.patterns(tid, "confirmation")
    submit_label, seen_captcha, result_text = "", None, ""
    from playwright.sync_api import sync_playwright
    profile = a.profile_dir or a.ws.path("browser_profile")
    report, last_url, dialogs = {}, {"url": form["form_url"]}, []
    status, reason = "prefilled", ""
    with sync_playwright() as pw:
        ctx, errors = None, []
        # A dedicated profile in any of these browsers - never the user's everyday profile.
        for channel in ("msedge", "chrome", None):
            try:
                ctx = pw.chromium.launch_persistent_context(profile, channel=channel, headless=a.headless,
                                                            viewport={"width": 1280, "height": 900}, locale="en-US")
                print("browser:", channel or "bundled chromium")
                break
            except Exception as e:
                errors.append(f"{channel or 'chromium'}: {str(e).splitlines()[0][:100]}")
        if ctx is None:
            sys.exit("could not start a browser: " + " | ".join(errors))
        ctx.set_default_timeout(ACTION_MS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("framenavigated", lambda fr: fr == page.main_frame and last_url.update(url=fr.url))
        # Informational alerts ("resume uploaded, review the data") block every other action until accepted.
        page.on("dialog", lambda d: (dialogs.append(d.message[:120]), d.accept()))
        page.goto(form["form_url"], wait_until="domcontentloaded", timeout=45000)
        wait_settle(page)
        if form.get("apply_click") and form["form_url"] == form["job_url"]:
            target = page.get_by_role("button", name=APPLY_TEXT)
            if target.count():
                target.first.click()
                wait_settle(page)
        if form.get("method_click"):
            exact = re.compile(r"^\s*" + re.escape(form["method_click"]) + r"\s*$", re.I)
            if not click_visible(page, exact):
                print(f"WARN apply option '{form['method_click']}' not found on the page")

        fields = dict(by_id)
        expected = {fid: answers.get(fid) for fid in fields}
        # Embedded forms (Comeet) load their iframe late, after the CV upload would already have been tried.
        for _ in range(30):
            if all(frame_for(page, u) for u in {f.get("frame_url", "") for f in fields.values()}):
                break
            page.wait_for_timeout(500)

        def fill_one(fid):
            f, value = fields[fid], expected[fid]
            frame = frame_for(page, f.get("frame_url", ""))
            if value in (None, "", []):
                try:  # the ATS may have filled it from the CV (work history, skills): keep that
                    current = read_value(frame, f) if frame and f["type"] != "file" else None
                except Exception:
                    current = None
                if current and not (isinstance(current, str) and norm(current) in ("please select", "select", "select an option")):
                    report[fid] = f"KEPT     {fid} | {f.get('label', '')} = {str(current)[:60]} (filled by the site)"
                else:
                    report[fid] = f"LEFT     {fid} | {f.get('label', '')}" + (" (required)" if f.get("required") else "")
                return
            try:
                if frame is None:
                    raise ValueError("frame not found")
                shown = fill_field(frame, f, value, a.cv if value == "CV" or f["type"] == "file" else None)
                report[fid] = f"FILLED   {fid} | {f.get('label', '')} = {shown}"
            except Exception as e:
                report[fid] = f"ERROR    {fid} | {f.get('label', '')}: {str(e).splitlines()[0][:150]}"

        # CV first: many ATS parse it and overwrite fields; then choices (they can reveal fields); then the rest.
        order = sorted(fields, key=lambda k: {"file": 0, "select": 1, "radio": 1, "buttons": 1, "combobox": 1}.get(fields[k]["type"], 2))
        for fid in order:
            fill_one(fid)
            if fields[fid]["type"] == "file" and expected[fid]:
                wait_settle(page)
                page.wait_for_timeout(2500)
                if form.get("method_click") and report[fid].startswith("FILLED"):
                    # Upload-first flows: Continue loads the data step, whose fields the rounds below answer from the pool.
                    step = click_visible(page, NEXT_TEXT)
                    print(f"STEP clicked '{step}' after the CV upload" if step else "WARN no Continue button after the CV upload")
                    page.wait_for_timeout(3000)
        # Read back, re-fill what the page reset, answer fields that appeared meanwhile.
        retriggered = False
        for round_no in range(4):
            page.wait_for_timeout(1500)
            changed = 0
            live, flags = scan_all(page)
            if form.get("method_click"):
                live = [f for f in live if not f.get("in_login")]
            known = {field_key(f): fid for fid, f in fields.items()}
            if pool:
                labels = [match_answers.plain(f.get("label")) for f in live]
                empty_choice = False
                for lf in live:
                    fid = known.get(field_key(lf))
                    if fid and fid.startswith("n") and expected[fid] in (None, "", []):
                        # A chained dropdown can appear before its options load: answer it again now.
                        value, entry, why = match_answers.answer_field(lf, labels, pool)
                        if value not in (None, "", []):
                            fields[fid], expected[fid] = lf, value
                            fill_one(fid)
                            report[fid] = report[fid].replace(f"{fid} |", f"{fid} | NEW", 1)
                            changed += 1
                        elif lf["type"] == "select" and lf.get("required") and len(set(lf.get("options") or [])) <= 1:
                            empty_choice = True
                        continue
                    if fid:
                        continue
                    fid = "n" + lf["field_id"][1:]
                    while fid in fields:
                        fid += "_"
                    value, entry, why = match_answers.answer_field(lf, labels, pool)
                    try:  # never overwrite what the site already filled (work history parsed from the CV)
                        current = read_value(frame_for(page, lf.get("frame_url", "")), lf) if lf["type"] != "file" else None
                    except Exception:
                        current = None
                    if current and not (isinstance(current, str) and norm(current) in ("please select", "select", "select an option")):
                        value, why = None, None
                    fields[fid], expected[fid] = lf, value
                    known[field_key(lf)] = fid
                    fill_one(fid)
                    report[fid] = report[fid].replace(f"{fid} |", f"{fid} | NEW", 1) + (f" [pool: {why}]" if why else "")
                    changed += 1
                    if value is None and lf["type"] == "select" and lf.get("required") and len(set(lf.get("options") or [])) <= 1:
                        empty_choice = True
                if empty_choice and not retriggered:
                    # Some ATS (Phenom) load a dependent list only on a real change of its parent: re-select the parents.
                    retriggered, changed = True, changed + 1
                    for fid, f in by_id.items():
                        if f["type"] == "select" and expected.get(fid) not in (None, "", []):
                            frame = frame_for(page, f.get("frame_url", ""))
                            try:
                                frame.locator(f["selector"]).first.select_option(index=0)
                                page.wait_for_timeout(800)
                            except Exception:
                                pass
                            fill_one(fid)
                            page.wait_for_timeout(1500)
            for fid, f in fields.items():
                if expected[fid] in (None, "", []) or not report.get(fid, "").startswith(("FILLED", "REFILLED")):
                    continue
                frame = frame_for(page, f.get("frame_url", ""))
                try:
                    got = read_value(frame, f)
                except Exception as e:
                    got = f"<unreadable: {str(e)[:60]}>"
                if same(expected[fid], got, f):
                    continue
                if round_no == 3:
                    report[fid] = f"MISMATCH {fid} | {f.get('label', '')}: expected {expected[fid]!r}, page has {got!r}"
                    continue
                fill_one(fid)
                if report[fid].startswith("FILLED"):
                    report[fid] = report[fid].replace("FILLED  ", "REFILLED", 1)
                changed += 1
            if not changed:
                break
        page.screenshot(path=os.path.join(out, "prefill.png"), full_page=True)
        lines = [report[k] for k in fields if k in report]
        left_required = [l for l in lines if l.startswith("LEFT") and l.endswith("(required)")]
        bad = [l for l in lines if l.startswith(("ERROR", "MISMATCH"))]
        summary = (f"filled {sum(l.startswith(('FILLED', 'REFILLED')) for l in lines)}, left {sum(l.startswith('LEFT') for l in lines)}, "
                   f"errors {len(bad)}, new fields {sum(k.startswith('n') for k in fields)}")
        print("\n".join(lines))
        for m in dialogs:
            print("DIALOG accepted:", m)
        print("SUMMARY", summary)

        if a.submit:
            _, flags = scan_all(page)
            seen_captcha = bool(flags["captcha"])
            if flags["captcha"] or flags["password"]:
                status, reason = "needs_human", "login or CAPTCHA on the page"
            elif left_required or bad:
                status, reason = "needs_human", f"{len(left_required)} required field(s) empty, {len(bad)} error(s)/mismatch(es)"
            else:
                button, label = find_submit(page, lib.patterns(tid, "submit_text"))
                submit_label = label
                if not button:
                    status, reason = "needs_human", "no submit button found" + (" (multi-step form)" if flags["next_button"] else "")
                else:
                    before = page.url
                    print(f"SUBMIT clicking '{label}'")
                    button.click()
                    wait_settle(page)
                    page.wait_for_timeout(5000)
                    text = page_text(page)
                    if ALREADY_TEXT.search(text):
                        status = "already_applied"
                    elif (CONFIRMATION_TEXT.search(text) or (confirm_extra and confirm_extra.search(text))
                          or re.search(r"thank", page.url, re.I)):
                        status = "submitted"
                    elif page.url == before and ERROR_TEXT.search(text):
                        status, reason = "validation_error", "the page reports missing/invalid fields after Submit"
                    else:
                        status, reason = "needs_human", "result page not recognised - check the browser"
                    page.screenshot(path=os.path.join(out, "result.png"), full_page=True)
                    result_text = text
                    if status in ("submitted", "already_applied"):
                        log_applied(applied_log, {"date": dt.date.today().isoformat(), "application": os.path.basename(out),
                                                  "status": status, "job_url": clean_url(form["job_url"]), "form_url": clean_url(form["form_url"])})
        print(f"STATUS {status}" + (f" - {reason}" if reason else ""))
        sys.stdout.flush()
        seen_status = ""
        if status in ("prefilled", "needs_human", "validation_error") and not a.headless:
            print("READY - finish the form in the browser window and close it when you are done.")
            sys.stdout.flush()
            deadline = time.time() + a.wait_minutes * 60
            # The user submits (CAPTCHA, last fields) and often closes the window right away: read the page while
            # it is open, so a confirmation is seen before the close. Text already on the form page does not count.
            try:
                baseline = page_text(ctx.pages[0])
            except Exception:
                baseline = ""
            while ctx.pages and time.time() < deadline:
                try:
                    p = ctx.pages[0]
                    p.wait_for_timeout(1500)
                    text = page_text(p)
                except Exception:
                    break
                hit = next((rx for rx in (ALREADY_TEXT, CONFIRMATION_TEXT, confirm_extra)
                            if rx and rx.search(text) and not rx.search(baseline)), None)
                if hit:
                    result_text = text
                    seen_status = "already_applied" if hit is ALREADY_TEXT else "submitted"
                    print(f"CONFIRMED {seen_status} - confirmation shown on the page; closing the window")
                    sys.stdout.flush()
                    p.wait_for_timeout(2000)
                    break
        final_text = ""
        try:
            if ctx.pages:
                final_text = page_text(ctx.pages[0])[:5000]
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
    if not seen_status and (CONFIRMATION_TEXT.search(final_text) or (confirm_extra and confirm_extra.search(final_text))):
        seen_status, result_text = "submitted", final_text
    confirmed = bool(seen_status) or status in ("submitted", "already_applied")
    if seen_status and status not in ("submitted", "already_applied"):
        # Submitted by the user in the window (CAPTCHA forms): logged like a scripted submit.
        log_applied(applied_log, {"date": dt.date.today().isoformat(), "application": os.path.basename(out),
                                  "status": seen_status, "job_url": clean_url(form["job_url"]), "form_url": clean_url(form["form_url"])})
        print(f"LOGGED {seen_status} (sent by the user in the browser) -> applied.csv")
    with open(os.path.join(out, "fill_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"form: {form['form_url']}\nSTATUS {status}{' - ' + reason if reason else ''}\n{summary}\n"
                 f"final_url: {last_url['url']}\nconfirmation_page_detected: {confirmed}\n\n" + "\n".join(lines) + "\n")
    print(f"CLOSED final_url={last_url['url']} confirmation_page_detected={confirmed}")
    if tid:
        final_status = seen_status or status
        try:
            lib.learn_fill(tid, final_status, submit_label=submit_label or None,
                           confirmation=confirmation_line(result_text, confirm_extra) if confirmed else None,
                           captcha=seen_captcha)
            print(f"TEMPLATE {tid} updated ({final_status})")
        except Exception as e:  # the library never blocks an application
            print(f"WARN site template not updated: {str(e)[:120]}")


def main(argv=None, ws=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jobpilot page", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    add_workspace_argument(common)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", parents=[common])
    e.add_argument("url")
    e.add_argument("--out", required=True)
    f = sub.add_parser("fill", parents=[common])
    f.add_argument("--form", required=True)
    f.add_argument("--answers", required=True)
    f.add_argument("--cv")
    f.add_argument("--out")
    f.add_argument("--profile-dir", help="browser profile (default: the workspace browser_profile)")
    f.add_argument("--pool", help="answers fields that appear while filling (default: the workspace answers pool)")
    f.add_argument("--submit", action="store_true", help="click Submit when every required field is filled and verified")
    f.add_argument("--applied-log", help="CSV of submitted applications (default: the workspace applied_log)")
    f.add_argument("--force", action="store_true", help="run even if the applied log says this job was already submitted")
    f.add_argument("--headless", action="store_true", help="no browser window (unattended runs; needs_human cases are left)")
    f.add_argument("--check", action="store_true", help="validate answers against the form, no browser")
    f.add_argument("--wait-minutes", type=int, default=120)
    a = ap.parse_args(argv)
    a.ws = ws or Workspace(a.workspace)
    if a.cmd == "fill" and not a.pool:
        a.pool = a.ws.path("answers_pool")
    {"extract": cmd_extract, "fill": cmd_fill}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
