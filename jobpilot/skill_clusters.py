"""Groups the demanded skills (demanded_skills_to_learn.csv) that name the same thing, for display only.

The CSV stays the single source and is never rewritten here. Two passes, both applied when the rows are read:
    local (free)    same normalized name, same words in another order, a one-term alias in parentheses or a
                    "/" part equal to another row's name ("Spark (PySpark)" ↔ "PySpark"), a near-identical
                    spelling (difflib ratio >= 0.9), a few obvious synonyms (k8s → kubernetes)
    semantic        skill_clusters.json in the workspace ({"canonical name": ["variant", ...]}), written by the
                    dashboard's "Grupează skill-urile noi" Claude task; only names it has not seen yet are sent

A group counts the distinct ads that asked for any of its names: two variants asked by two different ads count 2,
the same ad listing both counts once. Usage: merged(rows, clusters) → one row per group, most asked first.
"""
import difflib
import json
import os
import re
import unicodedata

SYNONYMS = {
    "k8s": "kubernetes", "gcp": "google cloud", "google cloud platform": "google cloud", "js": "javascript",
    "ts": "typescript", "postgres": "postgresql", "genai": "generative ai", "gen ai": "generative ai",
    "llms": "llm", "large language models": "llm", "ml": "machine learning", "ms sql": "sql server",
    "mssql": "sql server", "golang": "go", "node": "node.js", "nodejs": "node.js", "ci cd": "ci/cd",
}
PRIORITY = {"high": 3, "medium": 2, "low": 1}
SIMILAR = 0.9
PAREN = re.compile(r"\(([^()]*)\)")


def _plain(text, synonyms=True):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9+#./]+", " ", text).replace(" / ", "/").strip(" ./")
    text = re.sub(r"\s+", " ", text)
    return SYNONYMS.get(text, text) if synonyms else text


def _abbreviated(name):
    """A name the synonym table spells out ("k8s") - never chosen as a group's name while a full one exists."""
    base = PAREN.sub(" ", name or "")
    return _plain(base, synonyms=False) != _plain(base)


def _same_thing(base, inner):
    """Is a parenthesized term another name for the base ("Spark (PySpark)", "Retrieval-Augmented Generation (RAG)")
    rather than a qualifier ("Playwright (TypeScript)", "Kubernetes (AKS)")?"""
    b, i = base.replace(" ", ""), inner.replace(" ", "")
    if len(i) >= 3 and (i in b or b in i):
        return True
    initials = "".join(w[0] for w in re.split(r"[\s\-]+", base) if w)
    return len(i) >= 2 and i == initials


def keys(name):
    """A skill name → (main key, aliases). A parenthesized other name for the skill and "/" parts of 4+ letters are
    aliases; other parentheses ("RAG (embeddings, vector databases)", "Playwright (TypeScript)") are dropped."""
    base = PAREN.sub(" ", name or "")
    main = _plain(base)
    aliases = set()
    for inner in PAREN.findall(name or ""):
        if "," not in inner and _same_thing(main, _plain(inner)):
            aliases.add(_plain(inner))
    parts = [p.strip() for p in re.split(r"\s*/\s*", base)] if "/" in base else []
    if len(parts) > 1 and all(len(_plain(p)) >= 4 for p in parts):
        aliases |= {_plain(p) for p in parts}
    aliases.discard(main)
    aliases.discard("")
    return main, aliases


def _sorted_words(key):
    return " ".join(sorted(key.replace("/", " ").split()))


def load(ws):
    """skill_clusters.json → {canonical: [variants]} ({} when missing or broken)."""
    path = ws.path("skill_clusters")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: [v for v in vs if isinstance(v, str)] for k, vs in data.items()
            if isinstance(k, str) and not k.startswith("_") and isinstance(vs, list)}


def known_names(clusters):
    return {_plain(n) for canon, variants in clusters.items() for n in [canon, *variants]}


def groups(rows, clusters=None):
    """rows (CSV dicts with a "skill") → list of groups, each a list of row indexes, plus each group's canonical
    name from skill_clusters.json (None when the group is local only)."""
    clusters = clusters or {}
    n = len(rows)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    info = [keys(r.get("skill") or "") for r in rows]
    by_key = {}
    for i, (main, _) in enumerate(info):
        for k in (main, _sorted_words(main)):
            if k:
                if k in by_key:
                    union(i, by_key[k])
                else:
                    by_key[k] = i
    for i, (_, aliases) in enumerate(info):
        for a in aliases:
            j = by_key.get(a)
            if j is not None:
                union(i, j)
    mains = [m for m, _ in info]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = mains[i], mains[j]
            if len(a) >= 5 and len(b) >= 5 and find(i) != find(j) and \
                    difflib.SequenceMatcher(None, a, b).ratio() >= SIMILAR:
                union(i, j)
    canon_of = {}
    for canon, variants in clusters.items():
        for name in [canon, *variants]:
            canon_of[_plain(name)] = canon
    # the file lists exact names: match the whole name first ("X (a, b)" keeps its parentheses), then the main key
    file_canon = [canon_of.get(_plain(r.get("skill") or "")) or canon_of.get(info[i][0]) for i, r in enumerate(rows)]
    first_in = {}
    for i, canon in enumerate(file_canon):
        if canon is not None:
            if canon in first_in:
                union(i, first_in[canon])
            else:
                first_in[canon] = i
    out = {}
    for i in range(n):
        out.setdefault(find(i), []).append(i)
    result = []
    for members in out.values():
        canon = next((file_canon[i] for i in members if file_canon[i] is not None), None)
        result.append((members, canon))
    return result


def _jobs(row):
    return [j.strip() for j in (row.get("jobs") or "").split(" | ") if j.strip()]


def _times(row):
    try:
        return int(row.get("times_requested") or 0)
    except ValueError:
        return 0


def merged(rows, clusters=None):
    """One row per group, most asked first: skill (canonical, else the most asked full name), variants,
    times_requested (distinct ads), jobs,
    first_seen / last_seen, the highest priority, the status furthest along, category / mini_project / resource of
    the most asked member."""
    out = []
    for members, canon in groups(rows, clusters):
        ms = sorted((rows[i] for i in members),
                    key=lambda r: (_abbreviated(r.get("skill")), -_times(r), len(r.get("skill") or "")))
        jobs, extra = [], 0
        for r in ms:
            listed = _jobs(r)
            jobs += [j for j in listed if j not in jobs]
            extra += max(0, _times(r) - len(listed))   # asks counted without the ad's name
        names = [r.get("skill") or "" for r in ms]
        top = ms[0]
        statuses = {(r.get("status") or "").strip() for r in ms}
        status = ("learning" if "learning" in statuses else "done" if statuses == {"done"} else
                  top.get("status") or "")
        seen = [r.get("first_seen") for r in ms if r.get("first_seen")]
        last = [r.get("last_seen") for r in ms if r.get("last_seen")]
        out.append({
            **top,
            "skill": canon or names[0],
            "variants": [n for n in names if n != (canon or names[0])],
            "times_requested": len(jobs) + extra,
            "jobs": " | ".join(jobs),
            "first_seen": min(seen) if seen else top.get("first_seen", ""),
            "last_seen": max(last) if last else top.get("last_seen", ""),
            "priority": max((r.get("priority") or "" for r in ms), key=lambda p: PRIORITY.get(p.lower(), 0)),
            "status": status,
        })
    return sorted(out, key=lambda r: (-r["times_requested"], r["skill"].lower()))


def ungrouped(rows, clusters):
    """Names skill_clusters.json has not seen yet (the next Claude grouping gets only these)."""
    seen = known_names(clusters)
    return [r.get("skill") for r in rows if r.get("skill") and _plain(r["skill"]) not in seen]
