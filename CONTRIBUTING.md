# Contribuții

Mulțumesc că vrei să ajuți! Câteva reguli scurte, ca proiectul să rămână onest și ușor de întreținut.

## Principii
- **Maxim onest.** Nicio schimbare nu are voie să facă asistentul să inventeze angajatori, titluri, date, diplome, certificări sau cifre. Regulile din skill-uri și din `MASTER_SKILLS_POOL.md` (nivelurile A–E, „Do not claim yet”) rămân sursa de adevăr.
- **Omul decide.** Fără rezolvat CAPTCHA-uri, fără creat conturi sau tastat parole, fără LinkedIn. Întrebările din `do_not_answer` rămân la utilizator.
- **Fără date reale.** Testele, exemplele și capturile folosesc doar date fictive (Maria Ionescu, Example SRL, `example.com`). Nu adăuga CV-uri, răspunsuri sau nume de companii la care ai aplicat tu.

## Cum lucrezi
1. Skill-urile se modifică doar în `skills/`; apoi `python tools/build_bundles.py` regenerează `chatgpt/knowledge/`, `jobpilot/templates/` și `dist/`.
2. Codul pipeline-ului e în `jobpilot/`, al dashboard-ului în `dashboard/`. Căile vin mereu din workspace (`jobpilot/workspace.py`), niciodată scrise în cod.
3. Interfața: sursa e în `dashboard/web/src`. După modificări: `npm install && npm run build` în `dashboard/web` (Node 20+) și comiți și `dashboard/static/`.
4. Rulezi testele offline: `python -I jobpilot/tests/run_tests.py`. Un site care se poartă ciudat se repară generic în `jobpilot/page.py` sau în template-urile din `jobpilot/sites/`, cu un caz de test.
5. Fișierele JSON se scriu cu `json` (`ensure_ascii=False`, `indent=2`), iar CSV-urile cu modulul `csv` (UTF-8 cu BOM unde e cazul).

## Pull request-uri
- Spune ce problemă rezolvi și cum ai verificat (teste, simulare în dashboard).
- O schimbare pe PR; testele trebuie să treacă în CI.
- Interfața și documentația sunt în română; codul, comentariile și CV-urile generate sunt în engleză (plus română când anunțul e în română).
