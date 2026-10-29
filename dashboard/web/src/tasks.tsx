import { useEffect, useRef, useState } from "react";
import { api, Task, TaskEvent } from "./api";
import { short } from "./charts";
import { bold } from "./mirel";

const STATUS: Record<Task["status"], string> = {
  queued: "în coadă",
  running: "rulează",
  done: "gata",
  failed: "eșuat",
  cancelled: "anulat",
};

/** Recent tasks, kept current by the server's event stream; onFinish runs when a task ends. */
export function useTasks(onFinish: (t: Task) => void) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [lines, setLines] = useState<Record<number, string[]>>({});
  const finish = useRef(onFinish);
  finish.current = onFinish;

  useEffect(() => {
    api.tasks().then(setTasks);
    const source = new EventSource("/api/events");
    source.onmessage = (m) => {
      const e: TaskEvent = JSON.parse(m.data);
      if (e.type !== "line" && e.type !== "task") return; // chat events are Mirel's
      if (e.type === "line") {
        setLines((all) => ({ ...all, [e.task_id]: [...(all[e.task_id] ?? []), e.line] }));
        return;
      }
      setTasks((all) => [e.task, ...all.filter((t) => t.id !== e.task.id)].sort((a, b) => b.id - a.id).slice(0, 20));
      if (!["queued", "running"].includes(e.task.status)) finish.current(e.task);
    };
    source.onopen = () => api.tasks().then(setTasks); // catch up after a reconnect
    return () => source.close();
  }, []);

  return { tasks, lines };
}

const PDF_PATH = /output[\\/]pdf[\\/][^\s`'"<>|]+?\.pdf/i;

/** A finished CV task whose report names a PDF: its CV can be sent to a job link. */
export const canApplyWith = (t: Task) => t.kind === "cv" && t.status === "done" && PDF_PATH.test(t.result ?? "");

export function TaskList({ tasks, lines, onApplyLink }: {
  tasks: Task[];
  lines: Record<number, string[]>;
  onApplyLink?: (t: Task) => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const shown = open ?? tasks.find((t) => t.status === "running")?.id ?? null;
  if (!tasks.length) return <p className="muted">Nicio rulare încă.</p>;
  return (
    <ul className="tasks">
      {tasks.slice(0, 8).map((t) => (
        <li key={t.id} className={`task ${t.status}`}>
          <button className="task-head" onClick={() => setOpen(shown === t.id ? -1 : t.id)}>
            <span className={`dot ${t.status}`} />
            <b>{t.title}</b>
            <span className="muted">
              {STATUS[t.status]}
              {t.status === "running" && t.step !== null && t.steps.length > 1 && ` · pasul ${t.step + 1}/${t.steps.length}: ${t.steps[t.step]}`}
              {" · "}
              {(t.started_at ?? t.created_at).slice(11, 16)} UTC
              {t.usage && ` · ${short(t.usage.tokens)} tokeni` + (t.usage.cost_usd ? ` (~$${t.usage.cost_usd.toFixed(2)})` : "")}
            </span>
            {["queued", "running"].includes(t.status) && (
              <span
                className="link"
                onClick={(e) => {
                  e.stopPropagation();
                  api.cancelTask(t.id);
                }}
              >
                anulează
              </span>
            )}
          </button>
          {t.result && <TaskResult text={t.result} />}
          {onApplyLink && canApplyWith(t) && (
            <div className="result">
              <button className="mini" onClick={() => onApplyLink(t)}>🔗 Aplică la link cu acest CV…</button>
            </div>
          )}
          {shown === t.id && <TaskLog task={t} live={lines[t.id] ?? []} />}
        </li>
      ))}
    </ul>
  );
}

const CV_PATH = /output[\\/](?:pdf|docx)[\\/][^\s`'"<>|]+?\.(?:pdf|docx)/gi;

/** Claude's report for a task, with each CV file it names as a link. */
export function TaskResult({ text }: { text: string }) {
  const files = [...new Set(text.match(CV_PATH) ?? [])];
  return (
    <div className="result">
      <div className="report">{bold(text.replace(/^(PDF|JOB):.*$/gm, "").trim())}</div>
      {files.length > 0 && (
        <div className="files">
          {files.map((f) => (
            <a key={f} href={`/api/file?path=${encodeURIComponent(f)}`} target="_blank" rel="noreferrer">
              📄 {f.split(/[\\/]/).pop()}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function TaskLog({ task, live }: { task: Task; live: string[] }) {
  const [saved, setSaved] = useState<string[] | null>(null);
  const end = useRef<HTMLPreElement>(null);
  useEffect(() => {
    // the stored log up to now; lines arriving later come from the event stream
    api.task(task.id).then((t) => setSaved((t.log ?? "").split("\n").filter(Boolean)));
  }, [task.id, task.status === "running"]);
  const text = saved ? [...saved, ...live.slice(Math.max(0, live.length - liveAfter(saved, live)))] : live;
  useEffect(() => {
    end.current?.scrollTo(0, end.current.scrollHeight);
  }, [text.length]);
  return <pre ref={end} className="log">{text.join("\n") || "…"}</pre>;
}

/** How many of the live lines arrived after the saved log was read (the two overlap at the seam). */
function liveAfter(saved: string[], live: string[]) {
  for (let k = Math.min(saved.length, live.length); k >= 0; k--) {
    const tail = saved.slice(saved.length - k);
    if (tail.every((l, i) => l === live[i])) return live.length - k;
  }
  return live.length;
}
