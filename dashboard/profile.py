"""Profile and credentials for the dashboard: what the user is after (work mode, contract, salary, notice, up to 3
target titles) and the API keys in the workspace .env.

Where each setting lives (the files stay the single source; the dashboard only edits them):
    ANSWERS_POOL.json → screening      preferred_work_mode, preferred_contract_type, notice_period,
                                       earliest_start_date, willing_to_relocate, salary_expectation*
    job_sources.json  → filters        workplaces (remote / hybrid / onsite), allow_remote; title_include gains
                                       the target titles (never loses a term)
    scoring.json      → target_titles  (not part of the scoring request: changing it never triggers a rescore)
JSON files are written with ensure_ascii=False, indent=2, as the workspace rules ask.
"""
import json
import os
import re

CREDENTIALS = ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY")
WORKPLACES = ("remote", "hybrid", "onsite")
SALARY_KEYS = ("salary_expectation", "salary_expectation_ron_net_month", "salary_expectation_eur_net_month",
               "salary_expectation_ron_gross_year", "salary_expectation_eur_gross_year")
TEXT_KEYS = ("preferred_contract_type", "notice_period", "earliest_start_date", "willing_to_relocate")
MAX_TITLES = 3
WORK_MODE_TEXT = {   # how a set of workplaces reads in a form answer
    ("remote",): "Remote only",
    ("hybrid",): "Hybrid",
    ("onsite",): "On-site",
    ("hybrid", "remote"): "Remote or hybrid",
    ("onsite", "remote"): "Remote or on-site",
    ("hybrid", "onsite"): "Hybrid or on-site",
    ("hybrid", "onsite", "remote"): "Remote, hybrid or on-site (flexible)",
}


def _load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def modes_from_text(text):
    t = (text or "").lower()
    modes = [m for m, words in (("remote", r"remote"), ("hybrid", r"hybrid"), ("onsite", r"on-?site|office"))
             if re.search(words, t)]
    return modes or list(WORKPLACES)


def get(ws):
    screening = _load(ws.path("answers_pool")).get("screening", {})
    filters = _load(ws.path("job_sources")).get("filters", {})
    scoring = _load(ws.path("scoring"))
    return {
        "workplaces": filters.get("workplaces") or modes_from_text(screening.get("preferred_work_mode")),
        "preferred_work_mode": screening.get("preferred_work_mode", ""),
        **{k: screening.get(k, "") for k in TEXT_KEYS},
        "salary": {k: screening.get(k, "") for k in SALARY_KEYS},
        "target_titles": scoring.get("target_titles", []),
        "target_domain": scoring.get("target_domain", ""),
    }


def save(ws, changes):
    """Validates and writes the changed settings → the new profile. Raises ValueError on bad input."""
    pool_path, sources_path, scoring_path = ws.path("answers_pool"), ws.path("job_sources"), ws.path("scoring")
    if "workplaces" in changes:
        modes = tuple(sorted(set(changes["workplaces"] or ())))
        if not modes or any(m not in WORKPLACES for m in modes):
            raise ValueError("choose at least one of remote / hybrid / onsite")
    titles = None
    if "target_titles" in changes:
        titles = [re.sub(r"\s+", " ", t).strip() for t in changes["target_titles"] or [] if t and t.strip()]
        if len(titles) > MAX_TITLES:
            raise ValueError(f"at most {MAX_TITLES} target titles")
    for k in SALARY_KEYS:
        v = (changes.get("salary") or {}).get(k)
        if v not in (None, "") and k != "salary_expectation" and not str(v).isdigit():
            raise ValueError(f"{k} must be a whole number")

    pool = _load(pool_path)
    screening = pool.setdefault("screening", {})
    if "workplaces" in changes:
        screening["preferred_work_mode"] = WORK_MODE_TEXT[modes]
    for k in TEXT_KEYS:
        if k in changes:
            screening[k] = str(changes[k] or "").strip()
    for k, v in (changes.get("salary") or {}).items():
        if k in SALARY_KEYS:
            screening[k] = int(v) if k != "salary_expectation" and str(v).isdigit() else str(v or "").strip()
    _save(pool_path, pool)

    if "workplaces" in changes:
        sources = _load(sources_path)
        filters = sources.setdefault("filters", {})
        filters["workplaces"] = list(modes)
        filters["allow_remote"] = "remote" in modes
        _save(sources_path, sources)
    if titles is not None:
        scoring = _load(scoring_path)
        scoring["target_titles"] = titles
        _save(scoring_path, scoring)
        sources = _load(sources_path)
        include = sources.setdefault("filters", {}).setdefault("title_include", [])
        for t in titles:
            if t.lower() not in (i.lower() for i in include):
                include.append(t.lower())
        _save(sources_path, sources)
    return get(ws)


def set_top_n(ws, n):
    """Jobs per auto-apply run (apply.json top_n), 1-50. Raises ValueError."""
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 50:
        raise ValueError("numărul de joburi pe rulare trebuie să fie între 1 și 50")
    path = ws.path("apply_config")
    cfg = _load(path)
    cfg["top_n"] = n
    _save(path, cfg)
    return n


def set_credential(ws, name, value):
    """Writes (or with an empty value removes) NAME=value in the workspace .env; other lines are kept."""
    if name not in CREDENTIALS:
        raise ValueError(f"unknown credential {name!r}")
    value = (value or "").strip()
    if re.search(r"[\r\n\s]", value):
        raise ValueError("a key is one line without spaces")
    path = ws.path("env_file")
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f if l.split("=", 1)[0].strip() != name]
    if value:
        lines.append(f"{name}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return {"name": name, "set": bool(value), "overridden_by_environment": bool(os.environ.get(name))}
