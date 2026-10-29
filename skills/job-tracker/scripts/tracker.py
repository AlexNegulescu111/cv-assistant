"""Job-search tracker kept in plain CSV files, plus an interactive HTML notebook page (caiet).

Files (all UTF-8 with BOM, inside the workspace given by --root, default: current folder):
    tracker/notes.csv              to-dos: company + deadline + action
    tracker/applications.csv       one row per tailored CV / application
    demanded_skills_to_learn.csv   skills the market asks for and the user lacks

Commands:
    init
    note-add     --action A [--company C] [--deadline YYYY-MM-DD] [--kind K] [--priority P]
                 [--skill S] [--details D]
    note-update  ID [--status todo|doing|done] [--action ...] [--deadline ...] [...]
    note-delete  ID
    app-upsert   --id ID --company C --role R [--status S] [--draft F] [--cv F] [--languages EN,RO]
                 [--generic]
    skill-log    --skill S --category C --job "<Company> - <Role>" [--must-have]
                 [--mini-project M] [--resource R]
    skill-status --skill S --status "to learn"|learning|done
    agenda       overdue / today / next 7 days / later, plus top demanded skills without a note
    caiet        [--out tracker/caiet.html]  interactive notebook page with the data embedded
    import-changes FILE   apply caiet_changes.json exported from the page (FILE '-' = stdin)
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import secrets
import sys
import unicodedata

NOTE_FIELDS = ["id", "company", "action", "deadline", "kind", "priority", "skill", "status",
               "details", "created_at", "updated_at", "done_at"]
APP_FIELDS = ["id", "company", "role", "generic", "draft", "cv_files", "languages", "app_status",
              "status_changed_at", "updated"]
SKILL_FIELDS = ["skill", "category", "times_requested", "jobs", "first_seen", "last_seen",
                "priority", "status", "mini_project", "resource"]
KINDS = ["skill", "project", "apply", "cv", "followup", "interview", "other"]
APP_STATUSES = ["draft", "ready", "sent", "interview", "offer", "rejected"]
SKILL_STATUSES = ["to learn", "learning", "done"]


def paths(root):
    return {"notes": os.path.join(root, "tracker", "notes.csv"),
            "apps": os.path.join(root, "tracker", "applications.csv"),
            "skills": os.path.join(root, "demanded_skills_to_learn.csv")}


def read(path, fields):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k: (r.get(k) or "") for k in fields} for r in csv.DictReader(f)]


def write(path, fields, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today():
    return dt.date.today().isoformat()


def slug(text):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return s[:40] or "note"


def check_date(value):
    if value:
        dt.date.fromisoformat(value)
    return value


def cmd_init(a, p):
    workspace_id(a.root)
    for key, fields in (("notes", NOTE_FIELDS), ("apps", APP_FIELDS), ("skills", SKILL_FIELDS)):
        if os.path.exists(p[key]):
            print("exists", p[key])
        else:
            write(p[key], fields, [])
            print("created", p[key])


def cmd_note_add(a, p):
    rows = read(p["notes"], NOTE_FIELDS)
    base = f"n-{today().replace('-', '')}-{slug(a.action)}"
    nid, i = base, 2
    while any(r["id"] == nid for r in rows):
        nid, i = f"{base}-{i}", i + 1
    ts = now()
    rows.append({"id": nid, "company": a.company or "", "action": a.action,
                 "deadline": check_date(a.deadline or ""), "kind": a.kind or "other",
                 "priority": a.priority or "medium", "skill": a.skill or "", "status": "todo",
                 "details": a.details or "", "created_at": ts, "updated_at": ts, "done_at": ""})
    write(p["notes"], NOTE_FIELDS, rows)
    print("added", nid)


def cmd_note_update(a, p):
    rows = read(p["notes"], NOTE_FIELDS)
    row = next((r for r in rows if r["id"] == a.id), None)
    if not row:
        sys.exit(f"note not found: {a.id}")
    for k in ("company", "action", "deadline", "kind", "priority", "skill", "details", "status"):
        v = getattr(a, k)
        if v is not None:
            row[k] = check_date(v) if k == "deadline" else v
    if a.status is not None:
        row["done_at"] = now() if a.status == "done" else ""
    row["updated_at"] = now()
    write(p["notes"], NOTE_FIELDS, rows)
    print("updated", a.id)


def cmd_note_delete(a, p):
    rows = read(p["notes"], NOTE_FIELDS)
    left = [r for r in rows if r["id"] != a.id]
    if len(left) == len(rows):
        sys.exit(f"note not found: {a.id}")
    write(p["notes"], NOTE_FIELDS, left)
    print("deleted", a.id)


def cmd_app_upsert(a, p):
    rows = read(p["apps"], APP_FIELDS)
    row = next((r for r in rows if r["id"] == a.id), None)
    if row is None:
        row = {k: "" for k in APP_FIELDS}
        row.update(id=a.id, app_status="draft", status_changed_at=now())
        rows.append(row)
        print("NEW application", a.id)
    row.update(company=a.company, role=a.role, generic="yes" if a.generic else row["generic"] or "no",
               updated=today())
    if a.draft:
        row["draft"] = a.draft
    if a.cv:
        files = [f for f in row["cv_files"].split(" | ") if f]
        row["cv_files"] = " | ".join(files + [f for f in a.cv if f not in files])
    if a.languages:
        row["languages"] = a.languages
    if a.status and a.status != row["app_status"]:
        row["app_status"], row["status_changed_at"] = a.status, now()
    write(p["apps"], APP_FIELDS, rows)
    print("saved", a.id)


def cmd_skill_log(a, p):
    rows = read(p["skills"], SKILL_FIELDS)
    row = next((r for r in rows if r["skill"].lower() == a.skill.lower()), None)
    if row is None:
        rows.append({"skill": a.skill, "category": a.category, "times_requested": "1", "jobs": a.job,
                     "first_seen": today(), "last_seen": today(),
                     "priority": "high" if a.must_have else "medium", "status": "to learn",
                     "mini_project": a.mini_project or "", "resource": a.resource or ""})
        print("NEW skill", a.skill)
    else:
        jobs = [j for j in row["jobs"].split(" | ") if j]
        if a.job in jobs:
            print("already logged for this job:", a.skill)
            return
        row["jobs"] = " | ".join(jobs + [a.job])
        row["times_requested"] = str(int(row["times_requested"] or 0) + 1)
        row["last_seen"] = today()
        if a.must_have or int(row["times_requested"]) >= 3:
            row["priority"] = "high"
        row["mini_project"] = row["mini_project"] or a.mini_project or ""
        row["resource"] = row["resource"] or a.resource or ""
        print(f"INCREMENTED {row['skill']} -> {row['times_requested']}")
        if int(row["times_requested"]) >= 3:
            print(f"TOP {row['skill']} requested {row['times_requested']} times")
    write(p["skills"], SKILL_FIELDS, rows)


def cmd_skill_status(a, p):
    rows = read(p["skills"], SKILL_FIELDS)
    row = next((r for r in rows if r["skill"].lower() == a.skill.lower()), None)
    if not row:
        print("NOTE skill not in CSV:", a.skill)
        return
    row["status"] = a.status
    write(p["skills"], SKILL_FIELDS, rows)
    print("status", row["skill"], "->", a.status)


def buckets(notes):
    t = dt.date.today()
    out = {"overdue": [], "today": [], "next 7 days": [], "later": [], "no deadline": []}
    for n in notes:
        if n["status"] == "done":
            continue
        if not n["deadline"]:
            out["no deadline"].append(n)
            continue
        days = (dt.date.fromisoformat(n["deadline"]) - t).days
        key = "overdue" if days < 0 else "today" if days == 0 else "next 7 days" if days <= 7 else "later"
        out[key].append(n)
    for v in out.values():
        v.sort(key=lambda n: (n["deadline"], n["company"]))
    return out


def top_skills(skills, notes, n=3):
    noted = {x["skill"].lower() for x in notes if x["status"] != "done" and x["skill"]}
    open_skills = [s for s in skills if s["status"] != "done" and s["skill"].lower() not in noted]
    return sorted(open_skills, key=lambda s: -int(s["times_requested"] or 0))[:n]


def cmd_agenda(a, p):
    notes, skills = read(p["notes"], NOTE_FIELDS), read(p["skills"], SKILL_FIELDS)
    for name, items in buckets(notes).items():
        if not items:
            continue
        print(f"== {name}")
        for n in items:
            print(f"  [{n['deadline'] or '-'}] {n['company'] or '(general)'}: {n['action']} "
                  f"({n['kind']}, {n['priority']}, {n['status']}) id={n['id']}")
    tops = top_skills(skills, notes)
    if tops:
        print("== top demanded skills without an open note")
        for s in tops:
            print(f"  {s['skill']} x{s['times_requested']} | mini-project: {s['mini_project']}")


def meta_files(root):
    return (os.path.join(root, "tracker", "workspace_id.txt"), os.path.join(root, "tracker", "applied_ops.txt"))


def workspace_id(root):
    path = meta_files(root)[0]
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("ws-" + secrets.token_hex(6))
    return open(path, encoding="utf-8").read().strip()


def applied_ops(root):
    path = meta_files(root)[1]
    if not os.path.exists(path):
        return []
    return [l.strip() for l in open(path, encoding="utf-8") if l.strip()]


def find_template():
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, "..", "templates", "caiet.html"), os.path.join(here, "caiet.html"),
              "/mnt/data/caiet.html"):
        if os.path.exists(c):
            return c
    sys.exit("caiet.html template not found (expected in ../templates/ or next to tracker.py)")


def profile(root):
    path = os.path.join(root, "profile.json")
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8-sig"))
        except ValueError:
            print("WARN profile.json is not valid JSON")
    return {}


def cmd_caiet(a, p):
    notes, apps = read(p["notes"], NOTE_FIELDS), read(p["apps"], APP_FIELDS)
    skills = read(p["skills"], SKILL_FIELDS)
    prof = profile(a.root)
    for s in skills:
        s["id"] = slug(s["skill"])
        s["jobs"] = [j for j in s["jobs"].split(" | ") if j]
        s["companies"] = sorted({j.split(" - ")[0].strip() for j in s["jobs"]})
        s["times_requested"] = int(s["times_requested"] or 0)
    for x in apps:
        files = [f for f in x["cv_files"].split(" | ") if f]
        x["cv_docx"] = [f for f in files if f.lower().endswith(".docx")]
        x["cv_pdf"] = [f for f in files if f.lower().endswith(".pdf")]
        x["languages"] = [l.strip() for l in x["languages"].split(",") if l.strip()]
        x["generic"] = x["generic"] == "yes"
    name = prof.get("name", "")
    data = {
        "title": "Caiet Aplicări" + (f" – {name}" if name else ""),
        "workspace": os.path.basename(os.path.abspath(a.root)),
        "workspace_id": workspace_id(a.root),
        "notes": notes, "skills": skills, "applications": apps,
        "meta": {"last_sync": now()},
        "applied_ops": applied_ops(a.root),
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = open(find_template(), encoding="utf-8").read()
    if "/*__CAIET_DATA__*/null" not in page:
        sys.exit("caiet.html template has no data placeholder")
    page = page.replace("/*__CAIET_DATA__*/null", payload, 1)
    out = a.out or os.path.join(a.root, "tracker", "caiet.html")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print("saved", out)


NOTE_EDITABLE = ("company", "action", "deadline", "kind", "priority", "skill", "status", "details")


def cmd_import_changes(a, p):
    raw = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8-sig").read()
    payload = json.loads(raw)
    wid = workspace_id(a.root)
    if payload.get("workspace_id") and payload["workspace_id"] != wid:
        sys.exit(f"changes belong to another workspace ({payload['workspace_id']}, this one is {wid})")
    done = set(applied_ops(a.root))
    notes, apps = read(p["notes"], NOTE_FIELDS), read(p["apps"], APP_FIELDS)
    skills = read(p["skills"], SKILL_FIELDS)
    new_ids, counts = [], {}
    for o in payload.get("ops", []):
        oid, kind = o.get("op_id"), o.get("op")
        if not oid or oid in done:
            continue
        ts = o.get("at") or now()
        if kind == "note-add":
            d = o.get("data", {})
            row = {k: str(d.get(k) or "") for k in NOTE_FIELDS}
            row["id"] = row["id"] or f"n-{today().replace('-', '')}-{slug(row['action'])}"
            row["status"] = row["status"] or "todo"
            row["created_at"] = row["created_at"] or ts
            row["updated_at"] = row["updated_at"] or ts
            if not any(n["id"] == row["id"] for n in notes):
                notes.append(row)
        elif kind == "note-update":
            n = next((x for x in notes if x["id"] == o.get("id")), None)
            if n is None:
                print("WARN note not found:", o.get("id"))
            else:
                patch = o.get("patch", {})
                for k in NOTE_EDITABLE:
                    if k in patch:
                        n[k] = str(patch[k] or "")
                if "status" in patch:
                    n["done_at"] = str(patch.get("done_at") or ts) if patch["status"] == "done" else ""
                n["updated_at"] = ts
        elif kind == "note-delete":
            notes = [x for x in notes if x["id"] != o.get("id")]
        elif kind == "skill-status":
            s = next((x for x in skills if x["skill"] == o.get("skill")), None)
            if s is None or o.get("status") not in SKILL_STATUSES:
                print("WARN skill change skipped:", o.get("skill"), o.get("status"))
            else:
                s["status"] = o["status"]
        elif kind == "app-status":
            x = next((r for r in apps if r["id"] == o.get("id")), None)
            if x is None or o.get("status") not in APP_STATUSES:
                print("WARN application change skipped:", o.get("id"), o.get("status"))
            else:
                x["app_status"], x["status_changed_at"] = o["status"], ts
        else:
            print("WARN unknown op:", kind)
            continue
        new_ids.append(oid)
        counts[kind] = counts.get(kind, 0) + 1
    write(p["notes"], NOTE_FIELDS, notes)
    write(p["apps"], APP_FIELDS, apps)
    write(p["skills"], SKILL_FIELDS, skills)
    with open(meta_files(a.root)[1], "a", encoding="utf-8") as f:
        f.writelines(i + "\n" for i in new_ids)
    print("imported", len(new_ids), "change(s)", json.dumps(counts))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    n = sub.add_parser("note-add")
    n.add_argument("--action", required=True)
    n.add_argument("--company")
    n.add_argument("--deadline")
    n.add_argument("--kind", choices=KINDS)
    n.add_argument("--priority", choices=["high", "medium", "low"])
    n.add_argument("--skill")
    n.add_argument("--details")
    u = sub.add_parser("note-update")
    u.add_argument("id")
    for k in ("company", "action", "deadline", "skill", "details"):
        u.add_argument("--" + k)
    u.add_argument("--kind", choices=KINDS)
    u.add_argument("--priority", choices=["high", "medium", "low"])
    u.add_argument("--status", choices=["todo", "doing", "done"])
    sub.add_parser("note-delete").add_argument("id")
    ap_ = sub.add_parser("app-upsert")
    ap_.add_argument("--id", required=True)
    ap_.add_argument("--company", required=True)
    ap_.add_argument("--role", required=True)
    ap_.add_argument("--status", choices=APP_STATUSES)
    ap_.add_argument("--draft")
    ap_.add_argument("--cv", nargs="*")
    ap_.add_argument("--languages")
    ap_.add_argument("--generic", action="store_true")
    s = sub.add_parser("skill-log")
    s.add_argument("--skill", required=True)
    s.add_argument("--category", required=True)
    s.add_argument("--job", required=True)
    s.add_argument("--must-have", action="store_true")
    s.add_argument("--mini-project")
    s.add_argument("--resource")
    st = sub.add_parser("skill-status")
    st.add_argument("--skill", required=True)
    st.add_argument("--status", required=True, choices=SKILL_STATUSES)
    sub.add_parser("agenda")
    for name in ("caiet", "dashboard"):
        sub.add_parser(name).add_argument("--out")
    sub.add_parser("import-changes").add_argument("file")
    a = ap.parse_args()
    handlers = {"init": cmd_init, "note-add": cmd_note_add, "note-update": cmd_note_update,
                "note-delete": cmd_note_delete, "app-upsert": cmd_app_upsert, "skill-log": cmd_skill_log,
                "skill-status": cmd_skill_status, "agenda": cmd_agenda, "caiet": cmd_caiet, "dashboard": cmd_caiet,
                "import-changes": cmd_import_changes}
    handlers[a.cmd](a, paths(a.root))


if __name__ == "__main__":
    main()
