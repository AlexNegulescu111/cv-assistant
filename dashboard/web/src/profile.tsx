import { useEffect, useState } from "react";
import { api, Credentials, Profile, Task, Workplace } from "./api";
import { TaskResult } from "./tasks";

export const WORKPLACES: { id: Workplace; label: string }[] = [
  { id: "remote", label: "Remote" },
  { id: "hybrid", label: "Hibrid" },
  { id: "onsite", label: "La birou" },
];

// same texts as dashboard/profile.py WORK_MODE_TEXT (key: the sorted workplaces)
const WORK_MODE_TEXT: Record<string, string> = {
  remote: "Remote only",
  hybrid: "Hybrid",
  onsite: "On-site",
  "hybrid,remote": "Remote or hybrid",
  "onsite,remote": "Remote or on-site",
  "hybrid,onsite": "Hybrid or on-site",
  "hybrid,onsite,remote": "Remote, hybrid or on-site (flexible)",
};

export const CONTRACTS = [
  "Employment contract (full-time)",
  "Employment contract (part-time)",
  "B2B / contractor",
  "Full-time employment or B2B",
];

export const SALARY: { key: string; label: string }[] = [
  { key: "salary_expectation_ron_net_month", label: "RON net / lună" },
  { key: "salary_expectation_eur_net_month", label: "EUR net / lună" },
  { key: "salary_expectation_ron_gross_year", label: "RON brut / an" },
  { key: "salary_expectation_eur_gross_year", label: "EUR brut / an" },
  { key: "salary_expectation", label: "Text liber (formulare)" },
];

export function ProfileTab({ tasks }: { tasks: Task[] }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [draft, setDraft] = useState<Profile | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.profile().then((p) => {
      setProfile(p);
      setDraft(p);
    });
  }, []);
  if (!draft || !profile) return <p className="muted">Se încarcă…</p>;

  const set = (changes: Partial<Profile>) => setDraft({ ...draft, ...changes });
  const toggle = (w: Workplace) =>
    set({ workplaces: draft.workplaces.includes(w) ? draft.workplaces.filter((x) => x !== w) : [...draft.workplaces, w] });
  const titles = [...draft.target_titles, "", "", ""].slice(0, 3);
  const clean = (p: Profile) => JSON.stringify({ ...p, target_titles: p.target_titles.filter((t) => t.trim()), preferred_work_mode: "" });
  const dirty = clean(draft) !== clean(profile);
  const modeText = WORK_MODE_TEXT[[...draft.workplaces].sort().join(",")] ?? "—";

  const save = () =>
    api
      .saveProfile({
        workplaces: draft.workplaces,
        preferred_contract_type: draft.preferred_contract_type,
        notice_period: draft.notice_period,
        earliest_start_date: draft.earliest_start_date,
        willing_to_relocate: draft.willing_to_relocate,
        salary: draft.salary,
        target_titles: draft.target_titles.filter((t) => t.trim()),
      })
      .then(
        (p) => {
          setProfile(p);
          setDraft(p);
          setNote("Salvat în ANSWERS_POOL.json, job_sources.json și scoring.json.");
        },
        (e) => setNote(String(e)),
      );

  return (
    <section>
      <h2>Ce cauți</h2>
      <div className="row">
        <span className="label">Regim de lucru</span>
        <div className="chips">
          {WORKPLACES.map((w) => (
            <button key={w.id} className={draft.workplaces.includes(w.id) ? "chip on" : "chip"} onClick={() => toggle(w.id)}>
              {w.label}
            </button>
          ))}
        </div>
      </div>
      <p className="muted small">
        În formulare: „{modeText}”. Colectorul păstrează doar joburile cu regimul ales; joburile care nu-și
        spun regimul rămân.
      </p>
      <div className="row">
        <span className="label">Contract</span>
        <select value={draft.preferred_contract_type} onChange={(e) => set({ preferred_contract_type: e.target.value })}>
          {[...new Set([draft.preferred_contract_type, ...CONTRACTS])].filter(Boolean).map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </div>
      <div className="row">
        <span className="label">Titluri țintă (max 3)</span>
        <div className="titles">
          {titles.map((t, i) => (
            <input
              key={i}
              value={t}
              placeholder={`Titlul ${i + 1}`}
              onChange={(e) => {
                const next = [...titles];
                next[i] = e.target.value;
                set({ target_titles: next });
              }}
            />
          ))}
        </div>
      </div>
      <p className="muted small">Se adaugă la termenii căutați de colector; skills to learn se pot filtra după ele.</p>

      <h2>Pretenții și disponibilitate</h2>
      <div className="grid">
        {SALARY.map((s) => (
          <label key={s.key}>
            {s.label}
            <input
              value={draft.salary[s.key] ?? ""}
              inputMode={s.key === "salary_expectation" ? "text" : "numeric"}
              onChange={(e) => set({ salary: { ...draft.salary, [s.key]: e.target.value } })}
            />
          </label>
        ))}
        <label>
          Preaviz
          <input value={draft.notice_period} onChange={(e) => set({ notice_period: e.target.value })} />
        </label>
        <label>
          Data de început
          <input type="date" value={draft.earliest_start_date} onChange={(e) => set({ earliest_start_date: e.target.value })} />
        </label>
        <label>
          Relocare
          <input value={draft.willing_to_relocate} onChange={(e) => set({ willing_to_relocate: e.target.value })} />
        </label>
      </div>
      <div className="actions">
        <button className="primary" disabled={!dirty} onClick={save}>
          Salvează
        </button>
        {dirty && <button onClick={() => setDraft(profile)}>Renunță</button>}
        {note && <span className="muted small">{note}</span>}
      </div>

      <h2>Competențe, experiență, proiecte</h2>
      <CompetencyForm tasks={tasks} />
    </section>
  );
}

function CompetencyForm({ tasks }: { tasks: Task[] }) {
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const recent = tasks.filter((t) => t.kind === "competency").slice(0, 3);
  const submit = () =>
    api.startTask("competency", { text }).then(
      () => {
        setText("");
        setNote("Claude adaugă în MASTER_SKILLS_POOL.md după regulile add-competency (nivel de dovadă A–E).");
      },
      (e) => setNote(String(e)),
    );
  return (
    <>
      <textarea
        rows={4}
        value={text}
        placeholder="Ce ai învățat sau construit? Ex.: „Am terminat un proiect dbt + Postgres pe GitHub: modele staging/marts, teste, CI”."
        onChange={(e) => setText(e.target.value)}
      />
      <div className="actions">
        <button className="primary" disabled={text.trim().length < 20} onClick={submit}>
          Adaugă în profil
        </button>
        {note && <span className="muted small">{note}</span>}
      </div>
      {recent.map((t) => (
        <CompetencyResult key={t.id} task={t} />
      ))}
    </>
  );
}

function CompetencyResult({ task }: { task: Task }) {
  const [msg, setMsg] = useState<string | null>(null);
  const running = ["queued", "running"].includes(task.status);
  return (
    <div className="panel wide">
      <b>{task.title}</b> <span className="muted small">{running ? "în lucru…" : task.status}</span>
      {task.result && <TaskResult text={task.result} />}
      {task.result?.includes("```diff") && (
        <div className="actions">
          <button onClick={() => api.revertTask(task.id).then((r) => setMsg(r.message), (e) => setMsg(String(e)))}>
            Anulează modificarea
          </button>
          {msg && <span className="muted small">{msg}</span>}
        </div>
      )}
    </div>
  );
}

const CREDENTIALS: { key: string; label: string; secret?: boolean }[] = [
  { key: "TYPESAFE_API_KEY", label: "Cheie Jev (TypeSafe), pentru scoring", secret: true },
  { key: "claude_cli", label: "Claude Code CLI instalat" },
  { key: "claude_logged_in", label: "Claude Code CLI autentificat (în terminal: claude, apoi /login)" },
  { key: "ANTHROPIC_API_KEY", label: "Cheie API Anthropic (opțional: Claude CLI o folosește în locul abonamentului)", secret: true },
  { key: "codex_cli", label: "Codex CLI (alternativă, neintegrată încă)" },
  { key: "browser_profile", label: "Profil de browser cu autentificările pe site-uri" },
];

export function CredentialsTab() {
  const [creds, setCreds] = useState<Credentials | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const load = () => api.credentials().then(setCreds);
  useEffect(() => {
    load();
  }, []);

  const saveKey = (name: string, v: string) =>
    api.setCredential(name, v).then(
      (r) => {
        setEditing(null);
        setValue("");
        setNote(
          r.overridden_by_environment
            ? `${name} salvată în .env, dar variabila de mediu cu același nume are prioritate.`
            : `${name} ${r.set ? "salvată" : "ștearsă"} în .env.`,
        );
        load();
      },
      (e) => setNote(String(e)),
    );

  const openLogin = () =>
    api.startTask("login", { text: url }).then(
      () => setNote("Se deschide fereastra: te autentifici tu, apoi închizi fereastra. Sesiunea rămâne în profil."),
      (e) => setNote(String(e)),
    );

  return (
    <section>
      <ul className="creds">
        {CREDENTIALS.map((c) => (
          <li key={c.key}>
            <span className={creds?.[c.key] ? "ok" : "missing"}>{creds ? (creds[c.key] ? "✓" : "—") : "…"}</span> {c.label}
            {c.secret && editing !== c.key && (
              <>
                <button className="mini" onClick={() => setEditing(c.key)}>
                  {creds?.[c.key] ? "Schimbă" : "Setează"}
                </button>
                {creds?.[c.key] && (
                  <button className="mini" onClick={() => window.confirm(`Ștergi ${c.key} din .env?`) && saveKey(c.key, "")}>
                    Șterge
                  </button>
                )}
              </>
            )}
            {editing === c.key && (
              <span className="inline">
                <input type="password" autoComplete="off" value={value} placeholder="cheia" onChange={(e) => setValue(e.target.value)} />
                <button className="mini primary" disabled={!value.trim()} onClick={() => saveKey(c.key, value)}>
                  Salvează
                </button>
                <button className="mini" onClick={() => setEditing(null)}>
                  Renunță
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {note && <p className="muted small">{note}</p>}
      <p className="muted small">Cheile se scriu în .env din workspace; dashboard-ul arată doar dacă sunt setate, niciodată valoarea.</p>

      <h2>Autentificare pe site-uri</h2>
      <p className="muted small">
        Parolele nu se stochează și nu se tastează automat. Deschizi site-ul aici, te autentifici tu o dată, închizi
        fereastra; aplicările automate refolosesc apoi sesiunea din profilul de browser.
      </p>
      <div className="actions">
        <input className="grow" value={url} placeholder="https://firma.wd3.myworkdayjobs.com/…" onChange={(e) => setUrl(e.target.value)} />
        <button className="primary" disabled={!/^https?:\/\/\S+/.test(url)} onClick={openLogin}>
          Deschide fereastra
        </button>
      </div>
    </section>
  );
}
