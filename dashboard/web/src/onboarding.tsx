import { useEffect, useState } from "react";
import { api, Facts, Onboarding, OnboardingAnswers, Task, Workplace } from "./api";
import { ChatApi, ChatPanel } from "./mirel";
import { CONTRACTS, SALARY, WORKPLACES } from "./profile";
import { TaskResult } from "./tasks";

const GUIDE_CHAT: ChatApi = { state: api.guide, send: api.guideSend, reset: api.guideReset };

const LEVEL_TEXT: Record<string, string> = {
  A: "A · la job, în producție",
  B: "B · proiect personal",
  C: "C · curs / certificare",
  D: "D · știi să-l proiectezi",
  E: "E · înveți acum",
};

const CONTACT: { key: string; label: string }[] = [
  { key: "first_name", label: "Prenume" },
  { key: "last_name", label: "Nume" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Telefon" },
  { key: "city", label: "Oraș" },
  { key: "country", label: "Țară" },
  { key: "linkedin", label: "LinkedIn (URL)" },
  { key: "github", label: "GitHub" },
  { key: "portfolio", label: "Portofoliu / site" },
];

const SCREENING: { key: string; label: string; placeholder?: string }[] = [
  { key: "current_company", label: "Compania actuală" },
  { key: "current_title", label: "Titlul actual" },
  { key: "work_authorization", label: "Drept de muncă", placeholder: "ex. EU citizen, authorized to work in the EU" },
  { key: "requires_visa_sponsorship", label: "Ai nevoie de viză / sponsorizare?", placeholder: "No" },
  { key: "how_did_you_hear", label: "„How did you hear about us?”" },
];

const VOLUNTARY: { key: string; label: string }[] = [
  { key: "gender", label: "Gen" },
  { key: "ethnicity", label: "Etnie" },
  { key: "veteran_status", label: "Veteran" },
  { key: "disability", label: "Dizabilitate" },
];

const CONSENTS: { key: string; label: string }[] = [
  { key: "this_application_data", label: "Prelucrarea datelor pentru aplicarea respectivă" },
  { key: "future_recruitment", label: "Păstrarea datelor pentru alte posturi (talent pool)" },
  { key: "marketing", label: "Newsletter / marketing / alerte de joburi" },
];

const busyKind = (tasks: Task[], kind: string) => tasks.some((t) => t.kind === kind && ["queued", "running"].includes(t.status));
const lastDone = (tasks: Task[], kind: string) => tasks.find((t) => t.kind === kind && !["queued", "running"].includes(t.status));

/** Onboarding: CV → facts → form answers → guided questions → MASTER_SKILLS_POOL.md + ANSWERS_POOL.json. */
export function StartTab({ tasks, version, onOpenCredentials }: {
  tasks: Task[];
  version: number;
  onOpenCredentials: () => void;
}) {
  const [st, setSt] = useState<Onboarding | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => api.onboarding().then(setSt, (e) => setError(String(e)));
  useEffect(() => {
    load();
  }, [version]);
  if (!st) return error ? <p className="error">{error}</p> : <p className="muted">Se încarcă…</p>;
  const s = st.steps;
  const run = (kind: "onboard_extract" | "onboard_write") => {
    setError(null);
    api.startTask(kind).catch((e) => setError(String(e)));
  };
  const extract = lastDone(tasks, "onboard_extract");
  const write = lastDone(tasks, "onboard_write");

  return (
    <section className="onboarding">
      <p className="muted">
        {st.pool_exists
          ? "Ai deja un MASTER_SKILLS_POOL.md. Poți reface pașii cu un CV nou: la final pool-ul se completează, nu se rescrie."
          : "Bine ai venit! În câțiva pași construim cele două fișiere din care pleacă tot: MASTER_SKILLS_POOL.md (ce poți susține în CV, cu dovezi) și ANSWERS_POOL.json (răspunsurile pentru formulare). Nimic nu se inventează: Claude doar citește și reformulează ce confirmi tu."}
      </p>
      {error && <p className="error">{error}</p>}

      <Step n={1} title="CV-ul tău" done={s.cv}>
        <CvUpload st={st} onDone={setSt} />
      </Step>

      <Step n={2} title="Claude citește CV-ul" done={s.facts} locked={!s.cv}>
        <p className="muted small">
          Extrage faptele fixe (roluri, studii, certificări, limbi) și skill-urile cu un nivel de dovadă propus. Nu scrie încă nimic în pool.
        </p>
        <button className="primary" disabled={!s.cv || busyKind(tasks, "onboard_extract")} onClick={() => run("onboard_extract")}>
          {busyKind(tasks, "onboard_extract") ? "Citește…" : s.facts ? "Citește din nou" : "Citește CV-ul"}
        </button>
        {extract?.result && <TaskResult text={extract.result} />}
        {extract?.status === "failed" && <p className="error small">Citirea a eșuat; detaliile sunt în Aplică → Rulări.</p>}
      </Step>

      <Step n={3} title="Verifică faptele și nivelurile" done={s.confirmed} locked={!s.facts}>
        {st.facts && <FactsEditor facts={st.facts} levels={st.levels} onSaved={setSt} />}
      </Step>

      <Step n={4} title="Răspunsuri pentru formulare" done={s.answers} locked={!s.facts}>
        <AnswersForm st={st} onSaved={setSt} />
      </Step>

      <Step n={5} title="Întrebări care îți întăresc CV-ul" done={s.enrichment} locked={!s.confirmed}>
        <p className="muted small">
          Două runde de cel mult 6 întrebări: cifre pentru realizări, ce e în producție și câte o întrebare tehnică pentru
          afirmațiile importante (ca la interviu). Răspunsurile ajung în onboarding/enrichment.md.
        </p>
        <ChatPanel
          channel="onboarding"
          chat={GUIDE_CHAT}
          name="Ghidul"
          intro="Ghidul pornește de la faptele confirmate și te întreabă ce lipsește."
          onIdle={load}
        />
        {!s.enrichment && (
          <button className="mini" onClick={() => api.skipQuestions().then(setSt, (e) => setError(String(e)))}>
            Sari peste întrebări
          </button>
        )}
      </Step>

      <Step n={6} title="Scrie fișierele" done={s.written} locked={!(s.confirmed && s.answers && s.enrichment)}>
        <p className="muted small">
          Claude scrie MASTER_SKILLS_POOL.md, profile.json, AGENTS.md și completează ANSWERS_POOL.json și scoring.json.
          Vezi diferențele în raport; „Anulează” pune fișierele înapoi.
        </p>
        <button className="primary" disabled={!(s.confirmed && s.answers && s.enrichment) || busyKind(tasks, "onboard_write")}
          onClick={() => run("onboard_write")}>
          {busyKind(tasks, "onboard_write") ? "Scrie…" : s.written ? "Scrie din nou" : "Scrie fișierele"}
        </button>
        {write?.result && (
          <>
            <TaskResult text={write.result} />
            <button className="mini" onClick={() => api.revertTask(write.id).then(load, (e) => setError(String(e)))}>
              Anulează modificările
            </button>
          </>
        )}
      </Step>

      <Step n={7} title="Mai departe" done={false} locked={!s.written}>
        <ul className="next">
          <li>
            Pune cheia Jev (TypeSafe) pentru scorarea joburilor:{" "}
            <button className="mini" onClick={onOpenCredentials}>Credențiale</button>
          </li>
          <li>
            Vezi ce joburi ar găsi colectorul, fără să salveze nimic:{" "}
            <button className="mini" onClick={() => api.startTask("collect", { dry_run: true }).catch((e) => setError(String(e)))}>
              Colectează (simulare)
            </button>
          </li>
          <li>Trimiterea automată e oprită: verifici primele aplicări în „Așteaptă input”, apoi o pornești din ANSWERS_POOL.json și apply.json.</li>
        </ul>
      </Step>
    </section>
  );
}

function Step({ n, title, done, locked, children }: {
  n: number;
  title: string;
  done: boolean;
  locked?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`step${locked ? " locked" : ""}`}>
      <h2>
        <span className={done ? "step-n done" : "step-n"}>{done ? "✓" : n}</span> {title}
      </h2>
      {locked ? <p className="muted small">Mai întâi pașii de dinainte.</p> : children}
    </div>
  );
}

function CvUpload({ st, onDone }: { st: Onboarding; onDone: (s: Onboarding) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [extra, setExtra] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const send = () => {
    if (!file) return;
    if (st.steps.facts && !window.confirm("Un CV nou reia citirea de la început (faptele propuse se șterg). Continui?")) return;
    setSending(true);
    setNote(null);
    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result).split(",", 2)[1] ?? "";
      api.uploadCv(file.name, content, extra).then(
        (s) => {
          onDone(s);
          setNote(null);
          setSending(false);
        },
        (e) => {
          setNote(String(e));
          setSending(false);
        },
      );
    };
    reader.onerror = () => {
      setNote("Fișierul nu s-a putut citi.");
      setSending(false);
    };
    reader.readAsDataURL(file);
  };
  return (
    <>
      {st.cv.name && (
        <p className="small">
          Încărcat: <b>{st.cv.name}</b>{" "}
          {st.cv.text ? `· ${st.cv.chars} caractere citite` : "· PDF: Claude îl citește direct la pasul 2"}
        </p>
      )}
      <div className="row">
        <input type="file" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </div>
      <textarea
        rows={4}
        placeholder="Opțional: textul de pe LinkedIn (About, Experience, Skills), proiecte, cursuri, unelte pe care le folosești zilnic…"
        value={extra}
        onChange={(e) => setExtra(e.target.value)}
      />
      <div className="actions">
        <button className="primary" disabled={!file || sending} onClick={send}>
          {sending ? "Se încarcă…" : st.cv.name ? "Înlocuiește CV-ul" : "Încarcă CV-ul"}
        </button>
        <span className="muted small">PDF sau DOCX, max 10 MB. Rămâne în folderul onboarding/ al workspace-ului.</span>
      </div>
      {note && <p className="error small">{note}</p>}
    </>
  );
}

type ListKey = "roles" | "education" | "certifications" | "languages";
const LISTS: { key: ListKey; title: string; cols: { key: string; label: string }[] }[] = [
  { key: "roles", title: "Roluri (cel mai nou primul)", cols: [
    { key: "title", label: "Titlu" }, { key: "employer", label: "Angajator" }, { key: "start", label: "Început" },
    { key: "end", label: "Sfârșit" }, { key: "location", label: "Locație" }] },
  { key: "education", title: "Studii", cols: [
    { key: "degree", label: "Diplomă" }, { key: "institution", label: "Instituție" }, { key: "start", label: "Început" },
    { key: "end", label: "Sfârșit" }] },
  { key: "certifications", title: "Certificări și cursuri", cols: [
    { key: "name", label: "Nume" }, { key: "provider", label: "Furnizor" }, { key: "year", label: "An" }] },
  { key: "languages", title: "Limbi", cols: [{ key: "language", label: "Limbă" }, { key: "level", label: "Nivel" }] },
];

function FactsEditor({ facts, levels, onSaved }: { facts: Facts; levels: string[]; onSaved: (s: Onboarding) => void }) {
  const [draft, setDraft] = useState<Facts>(facts);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => setDraft(facts), [facts]);
  const setList = (key: ListKey | "skills", rows: unknown[]) => setDraft({ ...draft, [key]: rows } as Facts);
  const save = () =>
    api.saveFacts(draft).then(
      (s) => {
        onSaved(s);
        setNote("Confirmat.");
      },
      (e) => setNote(String(e)),
    );
  return (
    <>
      <p className="muted small">
        Corectează ce a citit Claude: faptele fixe apar identic în fiecare CV. La skill-uri, nivelul spune cum poate fi
        folosit: A și B în experiență, C la „Familiar with”, E doar la „Currently learning”.
      </p>
      <div className="grid">
        <label>Nume complet<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
        <label>Titlu (headline)<input value={draft.headline} onChange={(e) => setDraft({ ...draft, headline: e.target.value })} /></label>
      </div>
      {LISTS.map((l) => (
        <div key={l.key}>
          <h4>{l.title}</h4>
          <table className="edit">
            <thead>
              <tr>{l.cols.map((c) => <th key={c.key}>{c.label}</th>)}<th /></tr>
            </thead>
            <tbody>
              {(draft[l.key] as Record<string, string>[]).map((r, i) => (
                <tr key={i}>
                  {l.cols.map((c) => (
                    <td key={c.key}>
                      <input value={r[c.key] ?? ""} onChange={(e) => {
                        const rows = [...(draft[l.key] as Record<string, string>[])];
                        rows[i] = { ...rows[i], [c.key]: e.target.value };
                        setList(l.key, rows);
                      }} />
                    </td>
                  ))}
                  <td><button className="mini" title="Șterge" onClick={() => setList(l.key, (draft[l.key] as unknown[]).filter((_, j) => j !== i))}>×</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="mini" onClick={() => setList(l.key, [...(draft[l.key] as unknown[]), {}])}>+ adaugă</button>
        </div>
      ))}
      <h4>Skill-uri ({draft.skills.length})</h4>
      <table className="edit">
        <thead>
          <tr><th>Skill</th><th>Categorie</th><th>Nivel</th><th>Unde se vede</th><th /></tr>
        </thead>
        <tbody>
          {draft.skills.map((sk, i) => {
            const set = (k: string, v: string) => {
              const rows = [...draft.skills];
              rows[i] = { ...rows[i], [k]: v };
              setList("skills", rows);
            };
            return (
              <tr key={i}>
                <td><input value={sk.name} onChange={(e) => set("name", e.target.value)} /></td>
                <td><input value={sk.category} onChange={(e) => set("category", e.target.value)} /></td>
                <td>
                  <select value={sk.level} onChange={(e) => set("level", e.target.value)}>
                    {levels.map((lv) => <option key={lv} value={lv}>{LEVEL_TEXT[lv] ?? lv}</option>)}
                  </select>
                </td>
                <td><input value={sk.evidence} onChange={(e) => set("evidence", e.target.value)} /></td>
                <td><button className="mini" title="Șterge" onClick={() => setList("skills", draft.skills.filter((_, j) => j !== i))}>×</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button className="mini" onClick={() => setList("skills", [...draft.skills, { name: "", category: "", level: "C", evidence: "" }])}>+ adaugă</button>
      <div className="actions">
        <button className="primary" onClick={save}>Confirm faptele</button>
        {note && <span className="muted small">{note}</span>}
      </div>
    </>
  );
}

function AnswersForm({ st, onSaved }: { st: Onboarding; onSaved: (s: Onboarding) => void }) {
  const [a, setA] = useState<OnboardingAnswers>(st.answers);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => setA(st.answers), [st.answers]);
  const sec = <K extends "contact" | "screening" | "voluntary">(k: K, key: string, v: string) =>
    setA({ ...a, [k]: { ...a[k], [key]: v } });
  const prof = (changes: Partial<OnboardingAnswers["profile"]>) => setA({ ...a, profile: { ...a.profile, ...changes } });
  const toggle = (w: Workplace) =>
    prof({ workplaces: a.profile.workplaces.includes(w) ? a.profile.workplaces.filter((x) => x !== w) : [...a.profile.workplaces, w] });
  const titles = [...a.profile.target_titles, "", "", ""].slice(0, 3);
  const save = () =>
    api.saveAnswers({ ...a, profile: { ...a.profile, target_titles: a.profile.target_titles.filter((t) => t.trim()) } }).then(
      (s) => {
        onSaved(s);
        setNote("Salvat în ANSWERS_POOL.json, job_sources.json și scoring.json.");
      },
      (e) => setNote(String(e)),
    );
  return (
    <>
      <p className="muted small">Se completează o dată și se refolosesc în fiecare formular. Ce lași gol rămâne întrebare pentru tine.</p>
      <h4>Contact</h4>
      <div className="grid">
        {CONTACT.map((c) => (
          <label key={c.key}>{c.label}<input value={a.contact[c.key] ?? ""} onChange={(e) => sec("contact", c.key, e.target.value)} /></label>
        ))}
      </div>
      <h4>Ce cauți</h4>
      <div className="row">
        <span className="label">Regim de lucru</span>
        <div className="chips">
          {WORKPLACES.map((w) => (
            <button key={w.id} className={a.profile.workplaces.includes(w.id) ? "chip on" : "chip"} onClick={() => toggle(w.id)}>{w.label}</button>
          ))}
        </div>
      </div>
      <div className="row">
        <span className="label">Titluri țintă (max 3)</span>
        <div className="titles">
          {titles.map((t, i) => (
            <input key={i} value={t} placeholder={`Titlul ${i + 1}`} onChange={(e) => {
              const next = [...titles];
              next[i] = e.target.value;
              prof({ target_titles: next });
            }} />
          ))}
        </div>
      </div>
      <div className="row">
        <span className="label">Domeniul țintă</span>
        <input className="grow" value={a.target_domain} placeholder="ex. data, AI, automation or integration"
          onChange={(e) => setA({ ...a, target_domain: e.target.value })} />
      </div>
      <div className="row">
        <span className="label">Limbile CV-urilor</span>
        <div className="chips">
          {st.cv_languages.map((l) => (
            <button key={l} className={a.cv_languages.includes(l) ? "chip on" : "chip"} onClick={() =>
              setA({ ...a, cv_languages: a.cv_languages.includes(l) ? a.cv_languages.filter((x) => x !== l) : [...a.cv_languages, l] })}>{l}</button>
          ))}
        </div>
      </div>
      <div className="row">
        <span className="label">Contract</span>
        <select value={a.profile.preferred_contract_type} onChange={(e) => prof({ preferred_contract_type: e.target.value })}>
          <option value="">—</option>
          {[...new Set([a.profile.preferred_contract_type, ...CONTRACTS])].filter(Boolean).map((c) => <option key={c}>{c}</option>)}
        </select>
      </div>
      <h4>Pretenții, disponibilitate, drept de muncă</h4>
      <div className="grid">
        {SALARY.map((s) => (
          <label key={s.key}>{s.label}<input value={a.profile.salary[s.key] ?? ""} inputMode={s.key === "salary_expectation" ? "text" : "numeric"}
            onChange={(e) => prof({ salary: { ...a.profile.salary, [s.key]: e.target.value } })} /></label>
        ))}
        <label>Preaviz<input value={a.profile.notice_period} placeholder="ex. 30 days" onChange={(e) => prof({ notice_period: e.target.value })} /></label>
        <label>Data de început<input type="date" value={a.profile.earliest_start_date} onChange={(e) => prof({ earliest_start_date: e.target.value })} /></label>
        <label>Relocare<input value={a.profile.willing_to_relocate} placeholder="ex. No" onChange={(e) => prof({ willing_to_relocate: e.target.value })} /></label>
        {SCREENING.map((c) => (
          <label key={c.key}>{c.label}<input value={a.screening[c.key] ?? ""} placeholder={c.placeholder}
            onChange={(e) => sec("screening", c.key, e.target.value)} /></label>
        ))}
      </div>
      <h4>Întrebări voluntare (EEO)</h4>
      <div className="grid">
        {VOLUNTARY.map((c) => (
          <label key={c.key}>{c.label}<input value={a.voluntary[c.key] ?? ""} onChange={(e) => sec("voluntary", c.key, e.target.value)} /></label>
        ))}
      </div>
      <h4>Consimțăminte</h4>
      <p className="muted small">Doar ce alegi tu explicit; „Întreabă-mă” lasă bifa pentru tine în fiecare formular.</p>
      {CONSENTS.map((c) => (
        <div className="row" key={c.key}>
          <span className="label">{c.label}</span>
          <select value={a.consents[c.key] === true ? "yes" : a.consents[c.key] === false ? "no" : ""}
            onChange={(e) => setA({ ...a, consents: { ...a.consents, [c.key]: e.target.value === "yes" ? true : e.target.value === "no" ? false : null } })}>
            <option value="">Întreabă-mă</option>
            <option value="yes">Da</option>
            <option value="no">Nu</option>
          </select>
        </div>
      ))}
      <div className="actions">
        <button className="primary" onClick={save}>Salvează răspunsurile</button>
        {note && <span className="muted small">{note}</span>}
      </div>
    </>
  );
}
