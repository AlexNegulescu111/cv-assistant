"""Answer an application form from ANSWERS_POOL.json - no AI involved.

Usage:
    python -m jobpilot answers <dir>/form.json [--workspace DIR] [--pool ANSWERS_POOL.json] [--record] [--fresh]
        → <dir>/answers.json  {"answers": {"<field_id>": value}}  (format of job_page.py fill)
        → <dir>/pending.json  fields the pool could not answer, each with a reason:
              do_not_answer  - always the user's call (ID numbers, birth date, ...)
              ask            - the pool has the question but no answer yet → ask once, save with add-answer
              needs_writing  - free-text question → Claude writes it from the CV draft / MASTER_SKILLS_POOL.md
              no_option      - the pool answer is not among the field's options
              unknown        - required field no pattern matched → ask once, save with add-answer
    --record  increments times_used / last_used of the pool entries used (run once per real application);
              answers.json / pending.json are left as submitted
    Re-running keeps answers written earlier into answers.json for fields the pool cannot answer (KEPT lines);
    --fresh drops them.

Also imported by page.py to answer fields that appear only after other fields were filled.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata

CV_FILE = re.compile(r"resume|\bcv\b|curriculum", re.I)
COVER_FILE = re.compile(r"cover|motivation|scrisoare", re.I)
# Upload fields for other documents: never the CV.
OTHER_FILE = re.compile(r"reference|recommendation|certificate|certificat|diploma|transcript|portfolio|portofoliu|"
                        r"photo|picture|poz[ae]|\bid\b|identity|passport|work sample|referin", re.I)
CHOICE_TYPES = ("select", "radio", "combobox", "buttons")


def plain(text):
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().strip("*").strip().casefold()


def load_pool(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def expand(value, pool):
    if not isinstance(value, str):
        return value
    m = re.fullmatch(r"\{(\w+)\.(\w+)\}", value)
    if m:  # a lone reference keeps its type (numbers stay numbers)
        return pool.get(m.group(1), {}).get(m.group(2))
    return re.sub(r"\{(\w+)\.(\w+)\}", lambda m: str(pool.get(m.group(1), {}).get(m.group(2), "")), value)


def matches(patterns, label):
    return any(re.search(p, label, re.I) for p in patterns)


def option_labels(field):
    return [o["label"] if isinstance(o, dict) else o for o in field.get("options") or []]


def pick(options, prefs):
    """First preference present among the options (exact, then contained)."""
    opts = [(o, plain(o)) for o in options if plain(o) not in ("", "please select", "select", "selectati", "--")]
    for p in prefs:
        pp = plain(p)
        for o, po in opts:
            if po == pp:
                return o
        for o, po in opts:
            if pp and (contains(pp, po) or contains(po, pp)):
                return o
    return None


def contains(part, whole):
    """Whole-word containment: "da" must not pick "working holiday visa"."""
    return bool(part) and re.search(r"(?<!\w)" + re.escape(part) + r"(?!\w)", whole) is not None


def bool_for(field, value, prefs):
    """Consent answer (True/False) in the shape the field needs."""
    if field["type"] == "checkbox" and "options" not in field:
        return bool(value)
    opts = option_labels(field)
    if not opts:
        return "Yes" if value else "No"
    return pick(opts, prefs if value else ["No", "Nu", "I do not agree", "I disagree"])


def resolve(entry, field, form_labels, pool):
    """Answer of a question_bank entry for this field, or (None, reason)."""
    label = plain(field.get("label") or field.get("name"))
    answer, by_type = entry.get("answer"), entry.get("by_type", {})
    for v in entry.get("variants", []):
        # Both conditions given → both must hold.
        if ("when_form_has" in v or "when_label" in v) and \
           ("when_form_has" not in v or any(re.search(v["when_form_has"], l, re.I) for l in form_labels if l != label)) and \
           ("when_label" not in v or re.search(v["when_label"], label + " " + plain(field.get("hint")), re.I)):
            answer, by_type = v.get("answer", answer), v.get("by_type", by_type)
            break
    if field["type"] in by_type:
        answer = by_type[field["type"]]
    answer = expand(answer, pool)
    prefs = entry.get("option_prefs") or []
    if prefs == "voluntary":
        prefs = [answer] + pool.get("settings", {}).get("voluntary_default_options", [])
    if answer in (None, "") and not prefs:
        return None, "ask"
    if field["type"] in CHOICE_TYPES or (field["type"] == "checkbox" and "options" in field):
        opts = option_labels(field)
        if opts and entry.get("multi") and field["type"] == "checkbox":  # "select all that apply"
            chosen = []
            for p in prefs:
                c = pick(opts, [p])
                if c and c not in chosen:
                    chosen.append(c)
            return (chosen, None) if chosen else (None, "no_option")
        if opts:
            chosen = pick(opts, [p for p in [*prefs, answer] if p not in (None, "")])
            return (chosen, None) if chosen else (None, "no_option")
        if answer in (None, ""):
            return None, "ask"
    if field["type"] == "file":
        return None, "unknown"
    return answer, None


def answer_field(field, form_labels, pool):
    """→ (value, entry_id, reason). value None with reason None = leave empty (optional field)."""
    label = plain(field.get("label") or field.get("name"))
    required = field.get("required")
    if field["type"] == "file":
        if CV_FILE.search(label) or not (COVER_FILE.search(label) or OTHER_FILE.search(label)):
            return "CV", "cv_file", None  # "Upload options", "Attach" … are the CV upload
        return None, "other_file", ("unknown" if required else None)
    for e in pool.get("do_not_answer", []):
        if matches(e["patterns"], label):
            return None, e["id"], "do_not_answer"
    for e in pool.get("consents", []):
        if matches(e["patterns"], label):
            if e.get("answer") is None:
                return None, e["id"], "ask"
            v = bool_for(field, e["answer"], e.get("option_prefs") or [])
            return (v, e["id"], None) if v is not None else (None, e["id"], "no_option")
    for e in pool.get("question_bank", []):
        if matches(e["patterns"], label):
            value, reason = resolve(e, field, form_labels, pool)
            if reason == "ask" and e.get("optional_blank") and not required:
                return None, e["id"], None
            return value, e["id"], reason
    if field["type"] == "textarea" or matches(pool.get("free_text", {}).get("patterns", []), label):
        return None, "free_text", "needs_writing"
    return None, None, ("unknown" if required else None)


def match_form(fields, pool):
    labels = [plain(f.get("label") or f.get("name")) for f in fields]
    answers, pending, used = {}, [], []
    for f in fields:
        value, entry_id, reason = answer_field(f, labels, pool)
        answers[f["field_id"]] = value
        if entry_id and value is not None:
            used.append(entry_id)
        if reason:
            item = {"field_id": f["field_id"], "label": f.get("label", ""), "type": f["type"],
                    "required": bool(f.get("required")), "reason": reason, "pool_entry": entry_id}
            if option_labels(f):
                item["options"] = option_labels(f)
            if f.get("maxlength"):
                item["maxlength"] = f["maxlength"]
            pending.append(item)
    return answers, pending, used


def record_usage(pool_path, used):
    pool = load_pool(pool_path)
    today = dt.date.today().isoformat()
    for section in ("consents", "question_bank"):
        for e in pool.get(section, []):
            if e["id"] in used:
                e["times_used"] = e.get("times_used", 0) + used.count(e["id"])
                e["last_used"] = today
    with open(pool_path, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
        f.write("\n")


def keep_written(answers, pending, path):
    """Re-matching a form keeps the answers already written for fields the pool cannot answer (needs_writing,
    ask, unknown...) → (answers, pending without those fields, ids kept)."""
    if not os.path.exists(path):
        return answers, pending, []
    with open(path, encoding="utf-8") as f:
        old = json.load(f).get("answers", {})
    kept = [p["field_id"] for p in pending
            if p["reason"] != "do_not_answer" and old.get(p["field_id"]) not in (None, "", [])]
    for fid in kept:
        answers[fid] = old[fid]
    return answers, [p for p in pending if p["field_id"] not in kept], kept


def main(argv=None, ws=None):
    from .workspace import Workspace, add_argument as add_workspace_argument
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jobpilot answers", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("form")
    add_workspace_argument(ap)
    ap.add_argument("--pool", help="default: the workspace answers pool")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="drop answers written earlier for this form")
    a = ap.parse_args(argv)
    pool_path = a.pool or (ws or Workspace(a.workspace)).path("answers_pool")
    form = json.load(open(a.form, encoding="utf-8"))
    pool = load_pool(pool_path)
    answers, pending, used = match_form(form["fields"], pool)
    out = os.path.dirname(os.path.abspath(a.form))
    if a.record:
        # after submit: answers.json holds what was sent (incl. answers Claude wrote) - count usage, never overwrite it
        record_usage(pool_path, used)
        print(f"RECORDED {len(used)} pool entries; answers.json and pending.json left as submitted")
        return 0
    kept = []
    if not a.fresh:
        answers, pending, kept = keep_written(answers, pending, os.path.join(out, "answers.json"))
    for fid in kept:
        print(f"KEPT    {fid} (written earlier; --fresh to drop)")
    with open(os.path.join(out, "answers.json"), "w", encoding="utf-8") as f:
        json.dump({"answers": answers}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out, "pending.json"), "w", encoding="utf-8") as f:
        json.dump({"pending": pending}, f, ensure_ascii=False, indent=2)
    filled = sum(v is not None for v in answers.values())
    blocking = [p for p in pending if p["required"]]
    print(f"MATCHED {filled}/{len(answers)} fields | pending {len(pending)} ({len(blocking)} blocking)")
    for p in pending:
        print(f"PENDING {p['reason']:<13} {p['field_id']} | {p['label'][:80]}" + (" (required)" if p["required"] else ""))
    print("saved", os.path.join(out, "answers.json"), os.path.join(out, "pending.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
