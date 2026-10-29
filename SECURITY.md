# Securitate

## Ce protejează proiectul
- Datele tale (CV, răspunsuri, aplicări, chei) stau doar în workspace-ul local; pachetul nu are server propriu.
- Dashboard-ul ascultă doar pe `127.0.0.1`, acceptă doar cereri cu Host local, iar comenzile (POST/PUT/DELETE) doar de la propria pagină (verificare de Origin).
- Cheile din `.env` nu sunt afișate niciodată; API-ul spune doar dacă sunt setate.
- Pașii Claude rulează fără interfață, cu o listă de unelte permise: citire/scriere în workspace și `python -I`; fără web, fără browser. Textul din anunțuri și pagini e tratat ca date, nu ca instrucțiuni.

## Raportează o problemă
Nu deschide un issue public pentru o vulnerabilitate. Folosește **Security → Report a vulnerability** (GitHub private advisory) în acest repository, cu pașii de reproducere și impactul. Răspund de obicei în câteva zile.

Sunt în scop în special: ocolirea verificărilor Host/Origin, citirea fișierelor din afara folderelor permise prin `/api/file`, scurgerea cheilor din `.env`, prompt injection din anunțuri care face un pas Claude să iasă din regulile lui.
