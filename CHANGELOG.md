# Changelog

## 0.2.0 - 2026-10-29
Prima versiune publică.

- **Onboarding** (`jobpilot init` + tab-ul **Început**): CV PDF/DOCX → fapte și skill-uri cu nivel de dovadă propus → confirmare → răspunsuri pentru formulare → întrebări ghidate → `MASTER_SKILLS_POOL.md`, `ANSWERS_POOL.json`, `profile.json`, cu diff și anulare.
- **Costuri**: tokenii fiecărei rulări Claude și ai scorării Jev, pe zi și pe tip de rulare.
- **Skill-uri cerute**: top 10 cu grafic; numele care înseamnă același lucru sunt grupate (local + Claude, `skill_clusters.json`), numărând anunțurile distincte.
- **Aplicare automată**: fiecare rulare ia următoarele joburi peste prag; un job pe care Claude nu l-a putut scrie iese din rotație; `LEFT n` / `NO_CANDIDATES`.
- Valori implicite sigure pentru utilizatori noi: trimitere automată oprită, 10 aplicări pe zi.
- Oprire curată a dashboard-ului la Ctrl+C.
- Licență MIT.
