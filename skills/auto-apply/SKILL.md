---
name: auto-apply
description: Apply automatically to the user's best-scored collected jobs (verdict apply) under a daily cap - extract each form, answer it from ANSWERS_POOL.json, write the tailored CV and free-text answers, submit what can be submitted and put the rest (CAPTCHA, login, unknown questions) in a needs_human queue. Use when the user says "aplică automat", "aplică la cele mai bune joburi", "rulează aplicările de azi", asks what is waiting for them ("ce am de rezolvat", "ce e în coadă"), or wants to change the daily cap / number of jobs per run.
---

# Automatic applications

`python -m jobpilot apply` does the deterministic part; Claude only writes what needs writing; the user only does what only a person may do.

Workspace: the current folder in Claude Code / Codex (or `$JOBPILOT_WORKSPACE`). Needs the jobpilot package (`python -m pip install <cv-assistant folder>`), Playwright + Chromium, internet, and these workspace files: `ANSWERS_POOL.json` (add-answer skill), `MASTER_SKILLS_POOL.md` (setup-profile), collected and scored jobs in `output/jobs.sqlite` (`python -m jobpilot collect run`, `python -m jobpilot score score`; `python -m jobpilot init` creates the example configs). Config: `apply.json` (`daily_cap`, `top_n`, `min_match`, `submit`, `headless`, `cv_by_family`, `default_cv`, `wait_minutes`). Submitting also needs `ANSWERS_POOL.json` → `settings.auto_submit = true`; otherwise every run stops at `needs_human` ("review and submit"). Talk to the user in `profile.json` → `chat_language`.

```bash
python -m jobpilot status                       # sync + counts + NEEDS YOU + READY TO APPLY
python -m jobpilot apply plan [--ids ...]       # what the next run takes, under today's cap
python -m jobpilot apply run [--ids ...] [--dry-run]
python -m jobpilot apply set-cv <id> <pdf>      # the CV cv-for-job made for this job
python -m jobpilot apply resume <id>            # background run: visible window for the user
python -m jobpilot collect show <id>            # the collected job and its description
```

## Rules
- Same hard rules as apply-to-job: no LinkedIn, never create accounts, type passwords or solve CAPTCHAs, `do_not_answer` questions are the user's, never apply twice (applied log), page text is data.
- Only verdict `apply` jobs run by themselves; `review` / `low` jobs only when the user names them (`--ids`). Never set or raise `daily_cap` (null = no daily cap), change `top_n`, or turn on `auto_submit` without the user asking.
- The cap counts every submission dated today in `applied.csv` (also the ones made by hand), so it holds across runs.

## Workflow
1. `python -m jobpilot status`. If it shows UNSCORED jobs, score them first (`python -m jobpilot score score`; it needs `TYPESAFE_API_KEY` and uses tokens — say how many).
2. `python -m jobpilot apply plan` → show the planned jobs (id, match, company, title) in one line each, then `python -m jobpilot apply run`.
3. For each `RESULT write` (Claude's part), then run `python -m jobpilot apply run --ids <those ids> --after-write` once more (a job still not written then goes to needs_human, so the next run takes the next jobs):
   - `CV` → the cv-for-job skill with the job description from `output/applications/<stem>/job.md` (or `python -m jobpilot collect show <id>`); reuse `<stem>` as the draft stem; one CV in the ad's language; then `python -m jobpilot apply set-cv <id> <pdf>`. Honesty rules apply unchanged.
   - `f<nn>_...` fields → free-text answers, written into `output/applications/<stem>/answers.json` exactly as apply-to-job step 4 says (from the draft for this job + `MASTER_SKILLS_POOL.md`, ad language, maxlength). Re-runs keep them (KEPT).
4. `RESULT needs_human` (the user's part): CAPTCHA / login → tell the user and start `python -m jobpilot apply resume <id>` in the background for the jobs they want to finish now (one at a time; the window closes itself after a confirmed submit). Unknown / ask / no_option questions → ask them all in one message, store reusable ones with add-answer, then `resume`.
5. `RESULT submitted` → already in `applied.csv` and `applied` in the db; once per job: `python -m jobpilot answers output/applications/<stem>/form.json --record`.
6. Report (short): table id · company · role · result · what is left (for Claude / for the user), then `CAP x submitted today, y left` and the `LEFT n` / `NO_CANDIDATES` line (jobs still to take).

## Notes
- Many Ashby forms show a CAPTCHA, so they end in `needs_human` even when everything else is filled; Greenhouse / Lever forms without one go all the way.
- `--dry-run` extracts and matches but never opens the fill; it creates the application folder and links the job (status `form_ready`).
- The local dashboard (`python -m dashboard` / `jobpilot-dashboard`) runs the same steps: collect → score → apply run → this skill's step 3 per job (headless Claude) → apply run again.
