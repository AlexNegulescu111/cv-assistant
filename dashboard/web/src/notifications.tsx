import { useEffect, useRef, useState } from "react";
import { api, ApplyState, Notification, Notifications, Schedule, TaskEvent } from "./api";

const ICON: Record<Notification["kind"], string> = { needs_input: "✋", run: "▶", cv: "📄", error: "⚠" };

/** The bell: unread count, the list, and toasts on/off. onOpen(n) takes the user where the notification points. */
export function Bell({ onOpen }: { onOpen: (n: Notification) => void }) {
  const [data, setData] = useState<Notifications | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const load = () => api.notifications().then(setData);

  useEffect(() => {
    load();
    const source = new EventSource("/api/events");
    source.onmessage = (m) => {
      const e: TaskEvent = JSON.parse(m.data);
      if (e.type === "notification" || e.type === "notification_read") load();
    };
    return () => source.close();
  }, []);
  useEffect(() => {
    const close = (e: MouseEvent) => open && !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="bell-box" ref={box}>
      <button className="bell" onClick={() => setOpen(!open)} title="Notificări">
        🔔{data && data.unread > 0 && <span className="badge">{data.unread}</span>}
      </button>
      {open && data && (
        <div className="dropdown">
          <div className="dropdown-head">
            <b>Notificări</b>
            <label className="check small">
              <input type="checkbox" checked={data.toasts} onChange={(e) => api.setToasts(e.target.checked).then(setData)} />
              și pe desktop
            </label>
            {data.unread > 0 && (
              <button className="mini" onClick={() => api.markRead().then(setData)}>
                Marchează citite
              </button>
            )}
          </div>
          {!data.notifications.length && <p className="muted small">Nimic deocamdată.</p>}
          <ul>
            {data.notifications.map((n) => (
              <li key={n.id} className={n.read ? "" : "unread"}>
                <button
                  onClick={() => {
                    api.markRead([n.id]).then(setData);
                    setOpen(false);
                    onOpen(n);
                  }}
                >
                  <span className="icon">{ICON[n.kind]}</span>
                  <span>
                    <b>{n.title}</b>
                    {n.body && <span className="muted small"> {n.body}</span>}
                    <span className="muted small when">{n.at.slice(0, 16).replace("T", " ")} UTC</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** The daily auto-apply run (Windows Task Scheduler). */
export function DailyRun({ state }: { state: ApplyState | null }) {
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [time, setTime] = useState("09:00");
  const [dryRun, setDryRun] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    api.schedule().then((s) => {
      setSchedule(s);
      if (s.time) setTime(s.time);
      setDryRun(s.dry_run);
    });
  }, []);
  if (!schedule) return null;
  if (!schedule.supported) return <p className="muted small">Rularea zilnică are nevoie de Windows Task Scheduler.</p>;

  const save = () => {
    if (state?.submit && !dryRun &&
      !window.confirm(`În fiecare zi la ${time} se vor trimite aplicări reale (max ${state.top_n} pe rulare${state.daily_cap !== null ? `, ${state.daily_cap} pe zi` : ""}). Continui?`))
      return;
    api.saveSchedule(time, dryRun).then(
      (s) => {
        setSchedule(s);
        setNote(`Programat zilnic la ${s.time}${s.dry_run ? " (simulare)" : ""}. Rulează doar cât ești autentificat în Windows.`);
      },
      (e) => setNote(String(e)),
    );
  };
  const remove = () =>
    api.deleteSchedule().then(
      (s) => {
        setSchedule(s);
        setNote("Rularea zilnică a fost oprită.");
      },
      (e) => setNote(String(e)),
    );

  return (
    <>
      <div className="actions">
        <span>{schedule.enabled ? `Activă: zilnic la ${schedule.time}${schedule.dry_run ? " (simulare)" : ""}` : "Oprită"}</span>
        <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        <label className="check">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> doar simulare
        </label>
        <button className="primary" onClick={save}>
          {schedule.enabled ? "Actualizează" : "Programează"}
        </button>
        {schedule.enabled && <button onClick={remove}>Oprește</button>}
      </div>
      {note && <p className="muted small">{note}</p>}
      <p className="muted small">
        La ora aleasă rulează „Aplică automat”; rezultatul și joburile care te așteaptă apar la clopoțel și ca notificare
        Windows. Dacă dashboard-ul nu e deschis, rularea se face oricum, în fundal.
      </p>
    </>
  );
}
