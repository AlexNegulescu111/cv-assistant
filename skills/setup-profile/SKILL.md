---
name: setup-profile
description: Onboarding for the CV assistant - welcomes a new user and builds their workspace from scratch - turns an existing CV (PDF/DOCX), LinkedIn text and free-text descriptions into profile.json and a MASTER_SKILLS_POOL.md with evidence levels, creates the Caiet Aplicări page and the tracker files. Use on first contact, when profile.json or MASTER_SKILLS_POOL.md is missing, when the user uploads their CV for the first time, says "start", "set up", "configurează", "creează master pool", "pornește de la CV-ul meu", or wants to rebuild the pool.
---

# Onboarding: profile, master skills pool and caiet

The master pool is the single source of truth for every future CV. A good pool is **complete** (nothing the candidate really did is forgotten) and **honest** (every claim carries an evidence level the candidate can defend in an interview).

## Workspace

The workspace is the user's data folder: the current folder in Claude Code / Codex, `/mnt/data/workspace` in ChatGPT. Templates are in the `templates/` folder next to this file (in ChatGPT: knowledge files in `/mnt/data/`). Talk in the language the user writes in; store it as `chat_language`.

If `profile.json` already exists, ask whether to **update** (merge new input) or **rebuild** before touching anything.

## Workflow

### 0. Welcome (first contact only, one short message)
Explain in 3-4 lines what happens, then ask for the input:
> Salut! Te ajut să obții interviuri cu CV-uri adaptate fiecărui anunț, fără să inventăm nimic. Pașii: (1) îmi dai CV-ul și ce mai ai (LinkedIn, proiecte), (2) îți pun câteva întrebări ca să construiesc „master pool-ul” – lista ta de competențe cu dovezi, (3) de acum, când lipești un anunț, primești CV-ul adaptat (DOCX/PDF), analiza de potrivire și pregătirea de interviu. Am și un caiet de aplicări pentru deadline-uri și skill-uri de învățat.
> Pentru început: încarcă CV-ul (PDF/DOCX) și, dacă vrei, textul de pe LinkedIn. Spune-mi și ce roluri țintești și în ce limbi vrei CV-ul.

### 1. Collect the input
Accept whatever they have, all optional except one CV-like source:
- current CV (PDF / DOCX) or several versions;
- LinkedIn profile text (About, Experience, Skills, Certifications) or the LinkedIn PDF export;
- free text: projects, side work, courses, tools used daily, things they are proud of;
- GitHub / portfolio links;
- target roles (2-4 role families) and CV languages (e.g. EN, RO).

Extract text from files with Python (`pypdf`, `python-docx`) or the platform's file reader. Read everything before writing.

### 2. Fixed facts - confirm once
Show as a compact table for confirmation: full name, contact details, each role (title | employer | start - end | location), education, certifications (name | provider | year), languages with level. Resolve conflicts between sources by asking. These facts never change between CVs.

### 3. Skill and achievement inventory
For every skill, tool, achievement and project found, assign an evidence level:

| Level | Meaning | How CVs may use it |
|---|---|---|
| A - Work implementation | Done in a real job / for real users | Experience bullets, skills, summary |
| B - Personal project | Built end-to-end, ideally with a repo | Projects section, skills |
| C - Course / certification | Studied with exercises | Certifications, "Familiar with" |
| D - Architecture knowledge | Designed / evaluated, not implemented | Only as design knowledge, never as delivery |
| E - Learning | Currently studying | Only under "Currently Learning" |

A skill only listed in a skills section, with no role or project behind it, starts at C until the candidate explains where they used it.

### 4. Interview round (max 2 rounds, max 6 questions each, one message per round)
Prioritise questions that unlock the strongest CV material:
- metrics for the top achievements (volume, users, time saved, accuracy, money) - realistic estimates the candidate can defend are fine, invented numbers are not;
- production status (used by real users or a prototype?);
- for the 3-5 most important A/B claims, one concrete technical question each (e.g. "How did you handle failures in that pipeline?"). A claim the candidate cannot explain drops one level.
Anything still unknown goes to "Information to collect".

### 5. Write the files
1. `profile.json` from `templates/profile.template.json`: name, `file_prefix` (e.g. `Maria_Ionescu`, ASCII), default headline, contact, `chat_language`, `cv_languages`, `target_fields` (job areas in which missing skills are logged; derive from the target roles), `paper` (A4 in Europe, Letter in the US), `application_defaults` (current company/title from the newest role; other screening answers empty until the user gives them; voluntary disclosures "Prefer not to say"; used when cv-for-job pre-fills a form from a link), `created`/`updated` (today).
2. `MASTER_SKILLS_POOL.md` from `templates/MASTER_SKILLS_POOL.template.md`, in English, concise bullets:
   - Fixed facts exactly as confirmed;
   - Professional positioning (3-4 lines);
   - Core skill pool by category, every item tagged [A]-[E];
   - Evidence-based experience statements (verb + what + tech + result, only confirmed figures);
   - Projects (level B) with repo URLs;
   - Recommended CV variants: one per target role family (headline, 12-18 technologies, evidence to lead with);
   - Do not claim yet: tempting claims without evidence (seniority, tools only seen in a demo, level D items);
   - Information to collect; Usage rules (keep the template's rules).
3. `AGENTS.md` from `templates/AGENTS.template.md`, filling in the name and languages. It makes Codex and other agents that read `AGENTS.md` follow the same rules in this folder; harmless elsewhere. Skip in ChatGPT.
4. Tracker and caiet with the job-tracker script (Claude/Codex: `scripts/tracker.py` in the job-tracker skill folder; ChatGPT: `/mnt/data/tracker.py`):
   `python tracker.py --root <workspace> init` then `python tracker.py --root <workspace> caiet`, and show the caiet as the job-tracker skill describes.
5. Create empty `drafts/` and `output/` folders.

### 6. Report and hand-off
In the chat language, briefly:
- what was captured (roles, skills per level, projects) and the 3 strongest selling points;
- the most valuable open questions (from "Information to collect");
- the caiet link/path;
- next step: "Lipește un anunț de job și îți fac CV-ul adaptat." Offer also a general CV for the main target role (the cv-for-job skill with the role family instead of an ad).

In ChatGPT, zip the whole workspace as `cv_workspace.zip`, give the download link and say: upload it at the start of every new conversation (ChatGPT does not keep files between chats).

## Rules
- Never invent employers, titles, dates, degrees, certifications or metrics.
- The workspace contains personal data: never include it when the user shares the assistant itself.
