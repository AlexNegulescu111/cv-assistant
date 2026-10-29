# Configurare în ChatGPT

Există trei variante. Alege-o pe prima disponibilă în contul tău:

| Variantă | Pentru cine | Avantaj |
|---|---|---|
| **A. Custom GPT** | Plus, Business, Enterprise (oricine poate crea GPT-uri) | se partajează cu un link și merge pentru oricine are cont ChatGPT |
| **B. Skills native** | planurile care au „Skills” în ChatGPT (Business/Enterprise/Edu; disponibilitatea diferă) | aceleași fișiere `SKILL.md` ca în Claude și Codex |
| **C. Project** | oricine | fișierele rămân în proiect |

## A. Custom GPT (recomandat pentru partajare)
1. chatgpt.com → **Explore GPTs** → **Create** → tabul **Configure**.
2. **Name:** `CV Assistant` · **Description:** `CV-uri oneste și compatibile ATS, adaptate fiecărui anunț, plus un caiet de aplicări.`
3. **Instructions:** lipești tot conținutul din [GPT_INSTRUCTIONS.md](GPT_INSTRUCTIONS.md).
4. **Conversation starters:**
   - `Vreau să-mi configurez profilul - îți încarc CV-ul`
   - `Am un anunț de job, fă-mi un CV pentru el`
   - `Am terminat un proiect nou, adaugă-l în master pool`
   - `Ce am de făcut săptămâna asta?`
5. **Knowledge:** încarci **toate** fișierele din folderul `knowledge/`: 4 playbook-uri, 2 scripturi, șabloanele (inclusiv `caiet.html`) și exemplul fictiv.
6. **Capabilities:** bifezi **Code Interpreter & Data Analysis** (obligatoriu). Canvas e opțional.
7. **Create** → *Anyone with the link* pentru partajare.
8. Verifici cu [tests/PARITY_TEST.md](../tests/PARITY_TEST.md): încarci `test_workspace.zip` (din [Releases](https://github.com/AlexNegulescu111/cv-assistant/releases) sau `dist/`, după `python tools/build_bundles.py`) și lipești anunțul de test.

## B. Skills native în ChatGPT
Dacă vezi **Skills** în bara laterală a aplicației ChatGPT (desktop):
1. Adaugi pe rând cele 4 foldere din `skills/` (setup-profile, cv-for-job, add-competency, job-tracker), fiecare cu `SKILL.md`, `scripts/`, `templates/` și `agents/openai.yaml`. Poți încărca folderul ca atare sau zip-ul skill-ului din [Releases](https://github.com/AlexNegulescu111/cv-assistant/releases) (are același conținut). `apply-to-job` și `auto-apply` cer browser și internet (pachetul `jobpilot` local), deci nu le adaugi aici; `add-answer` e opțional.
2. Le invoci cu `@` (de exemplu `@cv-for-job`) sau lași ChatGPT să le aleagă singur după descriere.
3. Skill-urile adăugate pe web nu se sincronizează automat cu aplicația desktop, așa că le adaugi în ambele dacă le folosești în ambele.
4. Pentru workspace e valabilă aceeași regulă ca la A: încarci `cv_workspace.zip` la începutul conversației.
Meniurile diferă de la un plan la altul. Dacă nu găsești opțiunea, folosește varianta A.

## C. Project (fără GPT)
1. Creezi un **Project** → **Instructions**: lipești `GPT_INSTRUCTIONS.md`.
2. La **Files** încarci fișierele din `knowledge/` și, după onboarding, `cv_workspace.zip`.
3. După fiecare actualizare înlocuiești zip-ul din proiect cu cel nou.

## Cum se folosește
- **Prima dată:** încarci CV-ul (PDF/DOCX) și, opțional, textul de pe LinkedIn. Asistentul se prezintă, îți pune câteva întrebări, construiește master pool-ul și caietul.
- **La final** primești link-uri pentru: CV-uri (DOCX și PDF, dacă se poate), `caiet.html` și `cv_workspace.zip`. **Păstrează zip-ul**, pentru că ChatGPT nu reține fișierele între conversații.
- **Data viitoare:** încarci `cv_workspace.zip` în primul mesaj, apoi lipești anunțul.
- **Caietul** (`caiet.html`) se deschide în orice browser, inclusiv offline. Poți adăuga notițe și schimba statusuri direct pe pagină. Bannerul galben îți arată câte modificări n-au ajuns încă la asistent: apeși **Descarcă modificările** și încarci `caiet_changes.json` în chat, sau **Copiază** și lipești textul.
- Dacă PDF-ul nu se poate genera în sandbox, deschizi DOCX-ul în Word sau Google Docs și îl exporți în PDF.

## Confidențialitate
Când partajezi GPT-ul, ceilalți primesc doar instrucțiunile și fișierele din Knowledge, nu și datele tale. Nu pune niciodată `cv_workspace.zip` sau CV-ul tău în Knowledge-ul unui GPT partajat.
