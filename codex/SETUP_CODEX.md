# Configurare în Codex (CLI, aplicația desktop sau extensia IDE)

Codex citește skill-urile în formatul `SKILL.md` din `.agents/skills`, fie în folderul de lucru (până la rădăcina repo-ului), fie din `~/.agents/skills` pentru toate folderele. Fișierul `agents/openai.yaml` din fiecare skill îi dă numele afișat și promptul implicit.

## 1. Instalare (o singură dată)
Din folderul `cv-assistant`:
```bash
python tools/install.py --target codex
```
Skill-urile ajung în `~/.agents/skills/` și sunt disponibile în orice folder. Ca să le pui doar într-un anumit folder:
```bash
python tools/install.py --target codex --project "D:/Aplicari"
```
Ai nevoie de Python cu `python-docx` (și opțional `pypdf`, pentru numărul de pagini):
```bash
pip install python-docx pypdf
```
PDF-ul se face cu Microsoft Word (Windows) sau LibreOffice. Fără ele primești doar DOCX-ul.

Opțional, pentru modul „dau linkul și se completează formularul”:
```bash
pip install playwright
```
```bash
python -m playwright install chromium
```

## 2. Primul pas: onboarding
1. Creezi un folder gol pentru aplicări, de exemplu `D:/Aplicari`, pui în el CV-ul tău (PDF/DOCX) și pornești Codex acolo.
2. Scrii: `Vreau să-mi configurez profilul, CV-ul meu e în folder` sau invoci explicit `$setup-profile`.
3. Codex citește CV-ul, îți pune câteva întrebări și creează:
   - `profile.json` și `MASTER_SKILLS_POOL.md`;
   - `AGENTS.md`, regulile folderului, pe care Codex le citește automat la fiecare pornire;
   - `tracker/` cu `caiet.html`, caietul de aplicări.

## 3. Folosire zilnică
| Ce scrii | Ce se întâmplă |
|---|---|
| lipești un anunț de job sau dai **linkul** lui (sau `$cv-for-job` + anunțul) | analiză de potrivire, CV DOCX/PDF în `output/`, skill-uri notate, caiet actualizat, raport cu pregătire de interviu; cu link, se adaugă formularul precompletat într-o fereastră de browser, iar Submit îl apeși tu |
| `Am terminat cursul X / proiectul Y` | `$add-competency`: întrebări de verificare, apoi master pool actualizat |
| `Notează: follow-up la Exemplu SRL până vineri` | notiță în caiet |
| `Ce am de făcut?` | agenda pe deadline-uri |
| `Am modificat caietul, uite caiet_changes.json` | importă modificările făcute pe pagină |

Caietul îl deschizi cu `start tracker\caiet.html` (Windows) sau `open tracker/caiet.html` (macOS). Modificările făcute pe pagină le descarci cu butonul din bannerul galben și le dai lui Codex.

## 4. Aprobări
Codex rulează scripturile Python locale (`render_cv.py`, `tracker.py`) și, pe Windows, Word pentru PDF. În modul implicit îți poate cere aprobare la prima rulare; acceptă doar comenzi care rulează aceste scripturi în folderul de aplicări.

## Diferențe față de Claude
- Caietul se deschide ca fișier local în browser, nu ca artifact publicat; în rest comportamentul e același.
- Skill-urile sunt identice: aceleași fișiere `SKILL.md`, aceleași scripturi.
