import { useEffect, useState } from "react";
import { api, Usage, UsageTotals } from "./api";
import { short, StackedDays } from "./charts";

const KINDS: Record<string, string> = {
  cv: "CV din descriere lipită",
  write: "CV / texte la aplicarea automată",
  apply_link: "Aplicare la link",
  competency: "Competență nouă",
  mirel: "Mirel (chat)",
  onboarding: "Onboarding (chat)",
  onboard_extract: "Onboarding: citirea CV-ului",
  onboard_write: "Onboarding: scrierea pool-urilor",
  cluster_skills: "Gruparea skill-urilor",
  auto_apply: "Aplicare automată (alt pas)",
};

const SERIES = [
  { key: "claude", label: "Claude", color: "var(--series-1)" },
  { key: "jev", label: "Jev (scorare)", color: "var(--series-2)" },
];

const usd = (v: number | null | undefined) => (v ? `~$${v.toFixed(2)}` : "—");

function Card({ title, t }: { title: string; t: UsageTotals }) {
  return (
    <div className="card cost">
      <span className="muted small">{title}</span>
      <span className="num">{short(t.claude.tokens + t.jev.tokens)}</span>
      <span className="small">
        Claude {short(t.claude.tokens)} ({t.claude.runs} rulări, {usd(t.claude.cost_usd)}) · Jev {short(t.jev.tokens)} ({t.jev.jobs} joburi)
      </span>
    </div>
  );
}

export function CostsTab({ version }: { version: number }) {
  const [u, setU] = useState<Usage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.usage(30).then(setU, (e) => setError(String(e)));
  }, [version]);
  if (error) return <p className="error">{error}</p>;
  if (!u) return <p className="muted">Se încarcă…</p>;
  const m = u.periods.month.claude;
  return (
    <section>
      <div className="cards">
        <Card title="Azi" t={u.periods.today} />
        <Card title="Ultimele 7 zile" t={u.periods.week} />
        <Card title={`Ultimele ${u.days} zile`} t={u.periods.month} />
      </div>
      <p className="muted small">
        Tokeni Claude = intrare + ieșire + cache ({short(m.input)} / {short(m.output)} / {short(m.cache)} în {u.days} zile;
        citirile din cache costă mult mai puțin). Suma în $ e estimarea CLI-ului la prețul API; pe abonament nu e o
        factură. Jev: {u.averages.jev_per_job ? `în medie ${short(u.averages.jev_per_job)} tokeni pe job scorat` : "niciun job scorat"}.
      </p>
      <h2>Tokeni pe zi</h2>
      <StackedDays rows={u.series} series={SERIES} value={(r, k) => (k === "claude" ? r.claude : r.jev)} />
      <h2>Claude, pe tip de rulare</h2>
      {u.by_kind.length ? (
        <table>
          <thead>
            <tr><th>Tip</th><th className="num">Rulări</th><th className="num">Tokeni</th><th className="num">Medie / rulare</th><th className="num">Estimare</th></tr>
          </thead>
          <tbody>
            {u.by_kind.map((k) => (
              <tr key={k.kind}>
                <td>{KINDS[k.kind] ?? k.kind}</td>
                <td className="num">{k.runs}</td>
                <td className="num">{short(k.tokens)}</td>
                <td className="num">{short(k.avg_tokens)}</td>
                <td className="num">{usd(k.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">Nicio rulare Claude înregistrată încă (tokenii se notează de la această versiune).</p>
      )}
      {u.top_tasks.length > 0 && (
        <>
          <h2>Cele mai costisitoare rulări</h2>
          <table>
            <thead>
              <tr><th>Data</th><th>Rulare</th><th className="num">Tokeni</th><th className="num">Estimare</th></tr>
            </thead>
            <tbody>
              {u.top_tasks.map((t) => (
                <tr key={t.task_id}>
                  <td>{t.day}</td>
                  <td>{t.title ?? KINDS[t.kind] ?? t.kind}</td>
                  <td className="num">{short(t.tokens)}</td>
                  <td className="num">{usd(t.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
