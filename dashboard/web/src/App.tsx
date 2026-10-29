import { useCallback, useEffect, useState } from "react";
import { api, ApplyState, Category, Job, JobDetail, Overview, Skill, SkillsState, Task } from "./api";
import { TopBars } from "./charts";
import { CostsTab } from "./costs";
import { MirelTab } from "./mirel";
import { StartTab } from "./onboarding";
import { Bell, DailyRun } from "./notifications";
import { CredentialsTab, ProfileTab } from "./profile";
import { canApplyWith, TaskList, useTasks } from "./tasks";

type Tab = "start" | "apply" | "overview" | "costs" | "profile" | "credentials" | "mirel";

const TABS: { id: Tab; label: string }[] = [
  { id: "start", label: "Început" },
  { id: "apply", label: "Aplică" },
  { id: "overview", label: "Overview" },
  { id: "costs", label: "Costuri" },
  { id: "profile", label: "Profil" },
  { id: "credentials", label: "Credențiale" },
  { id: "mirel", label: "Mirel" },
];

const CARDS: { id: Category; label: string }[] = [
  { id: "applied", label: "Aplicate" },
  { id: "in_progress", label: "În curs" },
  { id: "needs_input", label: "Așteaptă input" },
];

const REASONS: Record<string, string> = {
  needs_writing: "text liber de scris",
  ask: "întrebare pentru tine",
  unknown: "fără răspuns în pool",
  no_option: "nicio opțiune potrivită",
  do_not_answer: "completezi tu",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("apply");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [category, setCategory] = useState<Category>("needs_input");
  const [error, setError] = useState<string | null>(null);

  const [version, setVersion] = useState(0); // bumped when a task ends, so open lists reload
  const refresh = useCallback(() => {
    api.overview().then(setOverview, (e) => setError(String(e)));
  }, []);
  useEffect(refresh, [refresh]);
  useEffect(() => {
    // a workspace without MASTER_SKILLS_POOL.md opens on the onboarding (unless a link asked for something else)
    api.onboarding().then((o) => o.needs_onboarding && !window.location.hash && setTab("start"), () => undefined);
  }, []);
  const { tasks, lines } = useTasks(() => {
    refresh();
    setVersion((v) => v + 1);
  });

  const openCategory = (c: Category) => {
    setCategory(c);
    setFocusJob(null);
    setTab("overview");
  };
  // a notification (or a toast's #job= / #task= link) takes the user to the job or the run
  const [focusJob, setFocusJob] = useState<number | null>(null);
  const go = (jobId: number | null, taskId: number | null) => {
    if (jobId) {
      api.job(jobId).then((j) => {
        setCategory(j.status === "needs_human" ? "needs_input" : j.status === "applied" ? "applied" : "in_progress");
        setFocusJob(jobId);
        setTab("overview");
      });
    } else if (taskId) {
      setTab("apply");
    }
  };
  useEffect(() => {
    const follow = () => {
      const m = window.location.hash.match(/^#(job|task)=(\d+)$/);
      if (m) go(m[1] === "job" ? Number(m[2]) : null, m[1] === "task" ? Number(m[2]) : null);
    };
    follow();
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);

  return (
    <div className="app">
      <header>
        <h1>Jobpilot</h1>
        <nav>
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
        <Bell onOpen={(n) => go(n.job_id, n.task_id)} />
      </header>
      {error && <p className="error">{error}</p>}
      <main>
        {tab === "start" && <StartTab tasks={tasks} version={version} onOpenCredentials={() => setTab("credentials")} />}
        {tab === "apply" && (
          <ApplyTab overview={overview} onOpen={openCategory} onRefresh={refresh} tasks={tasks} lines={lines} version={version} />
        )}
        {tab === "overview" && (
          <OverviewTab category={category} setCategory={setCategory} version={version} focusJob={focusJob} tasks={tasks} />
        )}
        {tab === "costs" && <CostsTab version={version} />}
        {tab === "profile" && <ProfileTab tasks={tasks} />}
        {tab === "credentials" && <CredentialsTab />}
        {tab === "mirel" && <MirelTab onChanged={refresh} />}
      </main>
    </div>
  );
}

function Cards({ overview, onOpen }: { overview: Overview | null; onOpen: (c: Category) => void }) {
  return (
    <div className="cards">
      {CARDS.map((c) => (
        <button key={c.id} className={`card ${c.id}`} onClick={() => onOpen(c.id)}>
          <span className="num">{overview ? overview.cards[c.id] : "…"}</span>
          <span>{c.label}</span>
        </button>
      ))}
    </div>
  );
}

function ApplyTab(props: {
  overview: Overview | null;
  onOpen: (c: Category) => void;
  onRefresh: () => void;
  tasks: Task[];
  lines: Record<number, string[]>;
  version: number;
}) {
  const [state, setState] = useState<ApplyState | null>(null);
  const [dryRun, setDryRun] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.applyState().then(setState).catch((e) => setError(`Setările de aplicare nu se pot citi: ${e}`));
  }, [props.version]);
  const busy = props.tasks.some((t) => t.kind === "auto_apply" && ["queued", "running"].includes(t.status));

  // a CV started from this page opens the link dialog as soon as it is done (a PDF in its report)
  const [waiting, setWaiting] = useState<number[]>([]);
  const [applyWith, setApplyWith] = useState<Task | null>(null);
  useEffect(() => {
    const ended = props.tasks.filter((t) => waiting.includes(t.id) && !["queued", "running"].includes(t.status));
    if (!ended.length) return;
    setWaiting((w) => w.filter((id) => !ended.some((t) => t.id === id)));
    const ready = ended.find(canApplyWith);
    if (ready) setApplyWith((open) => open ?? ready);
  }, [props.tasks]);

  const [topN, setTopN] = useState("");
  useEffect(() => {
    if (state) setTopN(String(state.top_n));
  }, [state?.top_n]);
  const saveTopN = () => {
    const n = Number(topN);
    if (!state || n === state.top_n) return;
    setError(null);
    api.setTopN(n).then(setState).catch((e) => {
      setError(String(e));
      setTopN(String(state.top_n));
    });
  };

  const start = () => {
    if (!state) {
      setError("Nu pot citi setările de aplicare (/api/apply-state). Repornește dashboard-ul și încearcă din nou.");
      return;
    }
    const real = state.submit && !dryRun;
    const most = state.remaining === null ? state.top_n : Math.min(state.remaining, state.top_n);
    if (real && !window.confirm(
      `Trimitere automată pornită: se pot trimite până la ${most} aplicări reale acum (cele mai bune scoruri Jev). Continui?`,
    )) return;
    setError(null);
    api.startTask("auto_apply", { dry_run: dryRun }).catch((e) => setError(String(e)));
  };

  return (
    <section>
      <Cards overview={props.overview} onOpen={props.onOpen} />
      {props.overview && props.overview.unscored > 0 && (
        <p className="muted">{props.overview.unscored} joburi noi încă nescorate.</p>
      )}
      <div className="actions">
        <button className="primary" onClick={start} disabled={busy}>
          {busy ? "Rulează…" : "▶ Aplică automat"}
        </button>
        <label className="check">
          Joburi pe rulare
          <input type="number" min={1} max={50} className="num-input" value={topN}
            onChange={(e) => setTopN(e.target.value)} onBlur={saveTopN}
            onKeyDown={(e) => e.key === "Enter" && saveTopN()} />
        </label>
        <label className="check">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Doar simulare (fără trimitere)
        </label>
        <button onClick={() => api.startTask("plan").catch((e) => setError(String(e)))}>Vezi planul</button>
        <button onClick={props.onRefresh}>↻ Reîmprospătează</button>
      </div>
      {state && (
        <p className="muted">
          Colectează → scorează → aplică la următoarele {state.top_n} joburi peste prag, după scorul Jev (fiecare
          rulare le ia pe următoarele; cele aplicate sau ajunse la „Așteaptă input” nu se mai iau). Mai sunt{" "}
          <b>{state.candidates_left}</b> joburi{state.apply_threshold !== null && ` cu scor ≥ ${state.apply_threshold}`}
          {state.candidates_left === 0 && " — rularea va colecta și scora joburi noi"}. Trimitere
          automată: <b>{state.submit ? "pornită" : "oprită"}</b> · azi {state.submitted_today} trimise
          {state.daily_cap !== null && ` (limită zilnică ${state.daily_cap})`}
          {!state.has_default_cv && " · Claude scrie CV-ul și textele libere pentru fiecare job, apoi aplică din nou"}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      <h2>Rulare zilnică</h2>
      <DailyRun state={state} />
      <h2>Generează CV personalizat</h2>
      <CvForm
        busy={props.tasks.some((t) => t.kind === "cv" && ["queued", "running"].includes(t.status))}
        onStarted={(t) => setWaiting((w) => [...w, t.id])}
      />
      <h2>Rulări</h2>
      <TaskList tasks={props.tasks} lines={props.lines} onApplyLink={setApplyWith} />
      {applyWith && <ApplyLinkDialog cvTask={applyWith} state={state} onClose={() => setApplyWith(null)} />}
    </section>
  );
}

function ApplyLinkDialog({ cvTask, state, onClose }: { cvTask: Task; state: ApplyState | null; onClose: () => void }) {
  const [url, setUrl] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const valid = /^https?:\/\/\S+$/i.test(url.trim());
  const linkedin = /^https?:\/\/([^/]*\.)?(linkedin\.com|lnkd\.in)(\/|$)/i.test(url.trim());
  const real = !!state?.submit && !dryRun;
  const send = () => {
    if (!valid || linkedin || sending) return;
    setSending(true);
    setError(null);
    api.startTask("apply_link", { text: url.trim(), source_task: cvTask.id, dry_run: dryRun }).then(onClose, (e) => {
      setError(String(e));
      setSending(false);
    });
  };
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="apply-link-title" onClick={(e) => e.stopPropagation()}>
        <h3 id="apply-link-title">CV gata — unde aplici?</h3>
        <p className="muted small">{cvTask.title.replace("CV personalizat — ", "")}</p>
        <input
          autoFocus
          className="wide-input"
          type="url"
          placeholder="https://… (pagina jobului sau formularul de aplicare)"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        {linkedin && <p className="error small">Linkurile LinkedIn nu se deschid; dă linkul de pe site-ul companiei sau ATS.</p>}
        <label className="check">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Doar simulare (fără trimitere)
        </label>
        <p className="muted small">
          Ca la aplicările automate: extrage formularul, răspunde din pool, Claude scrie textele libere, apoi{" "}
          {real ? <b>trimite aplicarea</b> : "se oprește înainte de trimitere"}. CAPTCHA, login sau întrebări noi →
          „Așteaptă input”.
        </p>
        {error && <p className="error small">{error}</p>}
        <div className="actions">
          <button className="primary" onClick={send} disabled={!valid || linkedin || sending}>
            {real ? "Aplică" : "Pregătește aplicarea"}
          </button>
          <button onClick={onClose}>Nu acum</button>
        </div>
      </div>
    </div>
  );
}

function CvForm({ busy, onStarted }: { busy: boolean; onStarted: (t: Task) => void }) {
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const submit = () =>
    api.startTask("cv", { text }).then(
      (t) => {
        onStarted(t);
        setText("");
        setNote("Pornit: Claude scrie CV-ul (câteva minute). Când e gata, se deschide fereastra pentru linkul la care aplici.");
      },
      (e) => setNote(String(e)),
    );
  return (
    <>
      <textarea
        placeholder="Lipește descrierea completă a jobului (titlu, companie, cerințe)…"
        rows={8}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="actions">
        <button className="primary" onClick={submit} disabled={text.trim().length < 200}>
          Generează CV
        </button>
        <span className="muted small">
          {busy ? "Un CV e deja în lucru; următorul intră în coadă." : "CV în limba anunțului, din MASTER_SKILLS_POOL.md, export PDF ≤ 2 pagini."}
        </span>
      </div>
      {note && <p className="muted small">{note}</p>}
    </>
  );
}

function OverviewTab(props: {
  category: Category;
  setCategory: (c: Category) => void;
  version: number;
  focusJob: number | null;
  tasks: Task[];
}) {
  const { category, setCategory } = props;
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [selected, setSelected] = useState<number | null>(props.focusJob);
  const [state, setState] = useState<SkillsState | null>(null);
  const skills = state?.skills ?? null;

  useEffect(() => {
    setJobs(null);
    setSelected(props.focusJob);
  }, [category, props.focusJob]);
  useEffect(() => {
    api.jobs(category).then(setJobs);
  }, [category, props.version]);
  const [titles, setTitles] = useState<string[]>([]);
  const [onlyTargets, setOnlyTargets] = useState(false);
  useEffect(() => {
    api.profile().then((p) => setTitles(p.target_titles));
  }, []);
  useEffect(() => {
    api.skills().then(setState);
  }, [props.version]);
  const grouping = props.tasks.some((t) => t.kind === "cluster_skills" && ["queued", "running"].includes(t.status));
  const [groupNote, setGroupNote] = useState<string | null>(null);
  const group = () =>
    api.startTask("cluster_skills").then(
      () => setGroupNote("Pornit: Claude grupează numele noi (rularea apare în Aplică → Rulări, costul în Costuri)."),
      (e) => setGroupNote(String(e)),
    );
  // a skill counts for the target titles when an ad that asked for it had one of their words in the role
  const words = titles.flatMap((t) => t.toLowerCase().split(/[^a-z0-9]+/)).filter((w) => w.length > 1);
  const shownSkills =
    skills && onlyTargets && words.length
      ? skills.filter((s) => words.some((w) => (s.jobs ?? "").toLowerCase().split(/[^a-z0-9]+/).includes(w)))
      : skills;

  return (
    <section>
      <div className="subtabs">
        {CARDS.map((c) => (
          <button key={c.id} className={category === c.id ? "active" : ""} onClick={() => setCategory(c.id)}>
            {c.label}
          </button>
        ))}
      </div>
      <div className="split">
        <JobList jobs={jobs} category={category} selected={selected} onSelect={setSelected} />
        {selected !== null && <JobPanel id={selected} version={props.version} onClose={() => setSelected(null)} />}
      </div>
      <h2>Skill-uri cerute: top 10</h2>
      <p className="muted small">
        Numele care înseamnă același lucru sunt grupate; un grup numără anunțurile distincte care l-au cerut
        {state && ` (${state.rows} nume în listă → ${state.skills.length} skill-uri)`}.
        {state && state.ungrouped > 0 && (
          <>
            {" "}
            <button className="mini" onClick={group} disabled={grouping}>
              {grouping ? "Se grupează…" : `Grupează skill-urile noi (${state.ungrouped})`}
            </button>
          </>
        )}
      </p>
      {groupNote && <p className="muted small">{groupNote}</p>}
      {titles.length > 0 && (
        <label className="check">
          <input type="checkbox" checked={onlyTargets} onChange={(e) => setOnlyTargets(e.target.checked)} /> Doar din
          anunțuri pentru titlurile țintă ({titles.join(", ")})
        </label>
      )}
      <SkillsTable skills={shownSkills} />
    </section>
  );
}

function JobList(props: { jobs: Job[] | null; category: Category; selected: number | null; onSelect: (id: number) => void }) {
  if (!props.jobs) return <p className="muted">Se încarcă…</p>;
  if (!props.jobs.length) return <p className="muted">Niciun job în această categorie.</p>;
  return (
    <table className="jobs">
      <thead>
        <tr>
          {props.category === "applied" && <th>Data</th>}
          <th>Companie</th>
          <th>Rol</th>
          <th>Match</th>
          <th>{props.category === "needs_input" ? "Motiv" : "Stare"}</th>
        </tr>
      </thead>
      <tbody>
        {props.jobs.map((j, i) => (
          <tr
            key={j.id ?? `log-${i}`}
            className={(j.id !== null ? "clickable " : "") + (j.id === props.selected ? "selected" : "")}
            onClick={() => j.id !== null && props.onSelect(j.id)}
          >
            {props.category === "applied" && <td>{j.applied_at}</td>}
            <td>{j.company ?? "—"}</td>
            <td>{j.url ? <a href={j.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{j.title}</a> : j.title}</td>
            <td>{j.match != null ? Math.round(j.match) : "—"}</td>
            <td>{props.category === "needs_input" ? j.reason ?? "—" : j.log_status ?? j.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JobPanel({ id, version, onClose }: { id: number; version: number; onClose: () => void }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  useEffect(() => {
    setJob(null);
    setOpened(null);
  }, [id]);
  useEffect(() => {
    api.job(id).then(setJob);
  }, [id, version]);
  const openForm = () =>
    api.startTask("resume", { job_id: id }).then(
      (t) => setOpened(t.status === "queued" ? "În coadă după rularea curentă…" : "Se deschide fereastra browserului…"),
      (e) => setOpened(String(e)),
    );
  if (!job) return <aside className="panel muted">Se încarcă…</aside>;
  return (
    <aside className="panel">
      <button className="close" onClick={onClose}>×</button>
      <h3>{job.title}</h3>
      <p className="muted">
        {job.company} · {job.location ?? ""} · {job.status}
        {job.score && ` · ${Math.round(job.score.match)} (${job.score.verdict})`}
      </p>
      {job.status === "needs_human" && (
        <>
          <button className="primary" onClick={openForm}>Deschide formularul precompletat</button>
          <p className="muted small">
            {opened ??
              "Se deschide o fereastră de browser cu formularul completat: rezolvi ce lipsește (login, CAPTCHA, întrebări) și trimiți; aplicarea se înregistrează singură."}
          </p>
        </>
      )}
      {job.reason && <p className="small">{job.reason}</p>}
      {job.pending.length > 0 && (
        <>
          <h4>Câmpuri în așteptare</h4>
          <ul>
            {job.pending.map((p) => (
              <li key={p.field_id}>
                {p.label} {p.required && <b>*</b>} <span className="tag">{REASONS[p.reason] ?? p.reason}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {job.has_prefill && <img className="prefill" src={`/api/jobs/${job.id}/prefill.png`} alt="Formular precompletat" />}
      {job.events.length > 0 && (
        <>
          <h4>Istoric</h4>
          <ul className="events">
            {job.events.map((e, i) => (
              <li key={i}>
                {e.at.replace("T", " ").replace("Z", "")} — {e.from_status ?? "–"} → {e.to_status} {e.note && <i>{e.note}</i>}
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}

function SkillsTable({ skills }: { skills: Skill[] | null }) {
  if (!skills) return <p className="muted">Se încarcă…</p>;
  if (!skills.length) return <p className="muted">Niciun skill cerut notat încă.</p>;
  const top = skills.slice(0, 10);
  return (
    <>
    <TopBars
      color="var(--series-1)"
      rows={top.map((s) => ({ label: s.skill, value: s.times_requested }))}
      note={(r) => {
        const s = top.find((x) => x.skill === r.label);
        return `${r.value} anunțuri` + (s?.variants.length ? ` · include: ${s.variants.join(", ")}` : "");
      }}
    />
    <table className="skills">
      <thead>
        <tr>
          <th>Skill</th>
          <th>Anunțuri</th>
          <th>Categorie</th>
          <th>Prioritate</th>
          <th>Stare</th>
        </tr>
      </thead>
      <tbody>
        {top.map((s) => (
          <tr key={s.skill}>
            <td title={[s.mini_project, s.resource].filter(Boolean).join(" · ")}>
              {s.skill}
              {s.variants.length > 0 && <div className="muted small">+ {s.variants.join(", ")}</div>}
            </td>
            <td className="num">{s.times_requested}</td>
            <td>{s.category}</td>
            <td>{s.priority}</td>
            <td>{s.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
    </>
  );
}

