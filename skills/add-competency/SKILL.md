---
name: add-competency
description: Add a new skill, achievement, project, course/certification or metric to the user's MASTER_SKILLS_POOL.md with the right evidence level, so future CVs can use it. Use when the user says they learned or built something new, finished a course or project, wants to "adaugă o competență/skill", provides a metric (volume, accuracy, time saved), or answers an open item from "Information to collect".
---

# Add a competency to the master pool

`MASTER_SKILLS_POOL.md` is the source of truth for every CV. A competency enters the pool with an evidence level, so later CVs claim it at the right strength and the candidate can defend it in an interview.

Workspace: the current folder in Claude Code / Codex, `/mnt/data/workspace` in ChatGPT. If the pool is missing, run the setup-profile skill first. Talk to the user in `profile.json` → `chat_language`.

## Workflow

### 1. Collect the evidence
Ask only for what is missing, in one short message:
- **What**: technology / skill / achievement.
- **Where**: current job, previous role, personal project, course.
- **What exactly they did**: built, configured, operated, migrated, analysed… and with which tools.
- **Result**: output, users, volume, time saved, accuracy - real or realistic estimates they can defend.
- **Proof** (optional but valuable): repo URL, certificate, demo link, date.

### 2. Assign the evidence level
| Level | Meaning | How CVs may use it |
|---|---|---|
| A - Work implementation | Done in a real job / for real users | Experience bullets, skills, summary |
| B - Personal project | Built end-to-end, ideally with a repo | Projects section, skills |
| C - Course / certification | Studied with exercises | Certifications, "Familiar with" |
| D - Architecture knowledge | Designed / evaluated, not implemented | Only as design knowledge, never as delivery |
| E - Learning | Currently studying | Only under "Currently Learning" |

### 3. Interview readiness check
Before recording level A or B, ask 2-3 concrete technical questions about it (e.g. for Airflow: "How did you schedule and retry the DAG? What happened on failure?"). If the answers are weak, record it one level lower and suggest what to practise.

### 4. Update the pool
Edit `MASTER_SKILLS_POOL.md`, keeping its structure and style (English, concise bullets):
- **Core skill pool**: add the skill to the right category with its [level] tag; no duplicates.
- **Evidence-based experience statements**: add or extend a bullet set (verb + what + tech + result); mark C/D/E items explicitly.
- **Projects available for CV use**: add level-B projects with 2-3 bullets and the repo URL.
- **Recommended CV variants / Compact skill lines**: add the skill where it strengthens a variant.
- **Do not claim yet**: remove an item only when level A/B evidence now exists.
- **Information to collect**: remove answered items and move the answer into the right section.
- **Certifications and courses** (fixed facts): add name, provider, year.

If the skill is in `demanded_skills_to_learn.csv`, update its status with the job-tracker script (Claude/Codex: `scripts/tracker.py` in the job-tracker skill folder; ChatGPT: `/mnt/data/tracker.py`):
`python tracker.py --root <workspace> skill-status --skill "<name>" --status done` (level A/B) or `learning` (level C/E).
If it closes an open note in `tracker/notes.csv`, mark that note done with `note-update <id> --status done`.
After any tracker change, regenerate the page with `caiet` (see the job-tracker skill for how to show it).

Update `profile.json` → `updated`.

### 5. Confirm
Tell the user briefly: what was added, at which level, where it can appear in CVs, and which CVs in `output/` would benefit from regeneration with the cv-for-job skill. For level C/E, suggest a small project that would raise it to level B. In ChatGPT, give back the updated workspace zip.
