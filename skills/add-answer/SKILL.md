---
name: add-answer
description: Add or change a reusable application-form answer in the user's ANSWERS_POOL.json (salary, notice period, start date, relocation, work authorisation, consents, "how did you hear", voluntary disclosures, any recurring screening question) so future forms are filled without asking. Use when apply-to-job or auto-apply left pending questions, when the user answers one of them, says "răspunde mereu…", "de acum salariul e…", "am alt preaviz", or wants to see/fix what the forms are answered with.
---

# Add an answer to the answers pool

`ANSWERS_POOL.json` is the source of truth for every application-form answer, the way `MASTER_SKILLS_POOL.md` is for CV content. `python -m jobpilot answers` answers a form from it with no AI involved, so each answer saved here removes a question from every future application.

Workspace: the current folder in Claude Code / Codex (or `$JOBPILOT_WORKSPACE`; `workspace.json` may move the pool). Needs the jobpilot package (`python -m pip install <cv-assistant folder>`). Talk to the user in `profile.json` → `chat_language`.

**No pool yet?** Copy `templates/ANSWERS_POOL.template.json` (next to this file) to the workspace as `ANSWERS_POOL.json`, then fill `contact` from `profile.json` → `contact` (split the name into first/last) and `screening` / `voluntary_disclosures` from `profile.json` → `application_defaults`. Empty values stay empty: the matcher asks for them (`ask`). `settings.auto_submit` stays `false` until the user says forms may be submitted automatically.

## Sections (keep this order; the matcher checks them in this order, first match wins)
| Section | Holds | Answer |
|---|---|---|
| `contact`, `screening`, `voluntary_disclosures` | the facts, referenced as `{section.key}` | values |
| `do_not_answer` | questions that always go to the user (ID numbers, birth date, criminal record, passwords) | none, never auto-filled |
| `consents` | checkbox / Yes-No consent texts | `true` / `false`; `null` = ask |
| `question_bank` | every other recurring question | text, with `option_prefs`, `by_type`, `variants` |
| `free_text.patterns` | questions written per job (motivation, "tell us", "anything to add") | written in apply-to-job, never stored |

`settings`: `auto_submit` (may the scripts click Submit), `voluntary_default_options` (the "prefer not to say" labels tried for `option_prefs: "voluntary"`).

Entry fields: `id`, `patterns` (Python regex, case-insensitive, matched against the label lower-cased without diacritics; anchor with `^…$` when a short word could appear inside other questions — e.g. `^company$` would otherwise match work-history rows), `answer`, `option_prefs` (option labels in order of preference; `"voluntary"` = the disclosure default list), `by_type` (`{"number": 4000, "date": "2026-01-15"}`), `variants` (`when_form_has` / `when_label` regex → other answer), `multi` (checkbox group: tick every `option_prefs` label present), `optional_blank` (leave empty when optional), `source` (`user (<company>, <date>)`), and `times_used` / `last_used` (written by `python -m jobpilot answers --record`, never by hand).

## Workflow

### 1. Collect the answer
- From `pending.json` files (`output/applications/*/pending.json`): group all `ask` / `unknown` / `no_option` items across applications so the user answers each question once. `do_not_answer` items are answered in the browser by the user, never stored.
- Ask in one short message, with the options when the field had options. Ask only what is missing.

### 2. Reusable or job-specific?
- Reusable (salary, notice, relocation, how you heard, consents, work authorisation, contract type…) → store.
- Job-specific (a referral name, "why this company", a one-off detail) → write it into that application's `answers.json` only; do not store.
- A changed fact (new salary, new start date, new notice) → update the value in `screening` / `contact`, not a new entry. Mention the old value in the confirmation.

### 3. Generalize
- Patterns: 2–5 phrasings that mean the same question, in English and in every language the user applies in (e.g. `notice` + `preaviz`), taken from the real label plus likely variants. Never one pattern per company.
- Choice fields: put the user's picked label and close synonyms in `option_prefs` (e.g. `["Job board", "Job Posting Sites", "Website"]`).
- Same question in different units (EUR vs local currency, number vs text, date vs text) → `variants` / `by_type` on one entry.

### 4. Check before saving
- No duplicate: search existing patterns; extend an entry instead of adding a near-copy.
- No conflict: a new pattern must not steal labels from another entry. Add the new label as a case to `answers_tests.json` (next to the pool; format in `scripts/check_answers.py`), then run
  `python scripts/check_answers.py` (next to this file; `--label "<label>" [--type select --options ...]` shows which entry answers one label). All cases must pass; a wrongly answered label always becomes a case.
- Consents: store `true` only for what the user explicitly agreed to; `null` keeps asking.
- Honesty: same rules as `cv-for-job` (no invented experience, years or certifications). "Years of experience with X" is never stored — derived per job from dated roles.
- Edit the JSON with a short Python script (`json` module, `ensure_ascii=False`, `indent=2`), not by hand.

### 5. Re-run pending applications
For each application whose pending items are now answered: `python -m jobpilot answers output/applications/<stem>/form.json` (answers written earlier are kept) and continue it with `apply-to-job` (or `python -m jobpilot apply resume <id>` for an auto-apply job).

### 6. Confirm
Tell the user briefly: what was added or changed (old → new value), which phrasings it now covers, and which pending applications it unblocked.
