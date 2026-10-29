# Test de paritate: același comportament în Claude, ChatGPT și Codex

Scop: verifici că asistentul face același lucru pe orice platformă când primește un anunț. Folosești un profil fictiv (Maria Ionescu) ca să nu expui datele tale.

## Pregătire
| Platformă | Cum pornești |
|---|---|
| ChatGPT (GPT sau Project) | încarci `test_workspace.zip` (Releases, sau `dist/` după `python tools/build_bundles.py`) în primul mesaj |
| Codex | copiezi conținutul din `tests/fixture_workspace/` într-un folder gol și pornești Codex acolo |
| Claude Code | la fel ca la Codex, cu skill-urile instalate prin `python tools/install.py --target claude` |

Apoi lipești textul din [sample_job_ad.md](sample_job_ad.md) cu mesajul: `Fă-mi un CV pentru acest anunț`.

## Ce trebuie să se întâmple (bifează)

### Analiză
- [ ] Tabel cu cerințele și nivelul Strong / Partial / Gap, plus un scor de potrivire (realist: aproximativ 55–75%) și recomandarea „aplică cu reformulare”.
- [ ] Niciun knock-out (are 3 ani de SQL/Python, engleză C1).
- [ ] Azure Data Factory apare ca Gap sau Partial (nivel D), **nu** ca experiență.

### Onestitate (cel mai important)
- [ ] Istoricul de angajare e identic cu pool-ul: 2 roluri, aceleași date.
- [ ] Nicio cifră nouă; apar doar 12 dashboard-uri, 40+ manageri, 6 h → 30 min, 5 surse.
- [ ] ADF, dbt, Databricks și PySpark **nu** apar ca experiență. dbt apare cel mult la „Currently Learning”, după ce asistentul te întreabă sau îți semnalează că o face.
- [ ] Nu apare „Machine learning in production”.

### Fișiere
- [ ] `drafts/exemplu_tech_junior_data_engineer_content.md` (sau un nume echivalent).
- [ ] `output/CV_Maria_Ionescu_Exemplu_Tech_Junior_Data_Engineer_EN.docx` și varianta **RO**, pentru că anunțul e în română; PDF dacă platforma poate converti.
- [ ] CV-ul are cel mult 2 pagini și niciun placeholder `[~` rămas.

### Caiet și skill-uri
- [ ] `demanded_skills_to_learn.csv` conține skill-urile lipsă (de exemplu Azure Data Factory, dbt, PySpark sau Databricks, CI/CD) cu jobul `Exemplu Tech - Junior Data Engineer`.
- [ ] `tracker/applications.csv` are un rând pentru acest job.
- [ ] `tracker/caiet.html` e regenerat și îți este arătat: link, download sau cale.
- [ ] **Nu** s-au creat notițe automat.

### Raport
- [ ] Respectă structura fixă: titlu și scor, tabel, „Ce am schimbat”, fișiere, întrebări pentru cifre (de exemplu volumul de date), gap-uri cu mini-proiecte, 5 întrebări de interviu, skill-uri notate, caiet și oferta de mesaj pentru recrutor.
- [ ] Este în română.
- [ ] În ChatGPT: la final primești link-uri pentru CV, `caiet.html` și `cv_workspace.zip`.

## Test 2: sincronizarea caietului
1. Deschizi `tracker/caiet.html`, marchezi aplicarea ca „Trimis” și adaugi o notiță „Follow-up Exemplu Tech” cu deadline peste 7 zile.
2. Apare bannerul galben → **Descarcă modificările** → încarci `caiet_changes.json` în chat cu mesajul „am actualizat caietul”.
- [ ] Asistentul rulează `import-changes`, apoi `caiet`; notița și statusul apar în CSV și în pagina nouă.
- [ ] Dacă reîncarci același fișier, nu apare nimic dublat.

## Test 3: competență nouă
Scrii: `Am terminat un mini-proiect dbt pe PostgreSQL, repo github.com/maria-ionescu-example/dbt-demo`.
- [ ] Asistentul pune 2–3 întrebări tehnice înainte să înregistreze dbt la nivel B.
- [ ] `MASTER_SKILLS_POOL.md` se actualizează: dbt trece de la [E] la [B] și se mută la Projects.
- [ ] dbt devine `done` în `demanded_skills_to_learn.csv`, iar caietul se regenerează.
- [ ] Asistentul propune regenerarea CV-ului Exemplu Tech.

## Test 4: link → formular precompletat (Claude Code / Codex)
Folosește un anunț public de pe Greenhouse sau Lever (de exemplu orice link `job-boards.greenhouse.io/...` sau `jobs.lever.co/...`) și scrie: `Fă-mi CV-ul pentru linkul ăsta`.
- [ ] Apare `output/applications/<stem>/job.md` cu descrierea și `form.json` cu câmpurile (Resume/CV, nume, email, întrebările).
- [ ] CV-ul se generează exact ca la testul cu text (pașii 1–7).
- [ ] Asistentul arată tabelul câmp → răspuns → sursă și întreabă o singură dată ce nu știe; declarațiile și opțiunile de marketing rămân goale.
- [ ] După „da” se deschide o fereastră Edge/Chrome cu formularul completat și CV-ul atașat. **Nimic nu e trimis.**
- [ ] Un link `linkedin.com/...` e refuzat și ți se cere linkul companiei sau descrierea.
- [ ] Cu text lipit în loc de link nu se deschide niciun browser.

Dacă un punct pică pe o platformă, problema e aproape mereu că playbook-ul nu a fost citit integral. În ChatGPT, verifică dacă e bifat Code Interpreter și dacă toate fișierele din `chatgpt/knowledge/` sunt încărcate.
