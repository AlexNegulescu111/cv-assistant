import { useEffect, useRef, useState } from "react";
import { api, ChatMessage, MirelState, TaskEvent } from "./api";

const REASONS: Record<string, string> = {
  ask: "de confirmat",
  unknown: "fără răspuns în pool",
  no_option: "nicio opțiune potrivită",
};

const TOOLS: Record<string, string> = {
  Read: "citește un fișier",
  Edit: "editează",
  Write: "scrie",
  Bash: "rulează un script",
  PowerShell: "rulează un script",
  Grep: "caută",
  Glob: "caută fișiere",
  Skill: "deschide un skill",
};

export interface ChatApi {
  state: () => Promise<MirelState>;
  send: (text?: string) => Promise<{ ok: boolean }>;
  reset: () => Promise<{ ok: boolean }>;
}

/** A chat kept by the server (one Claude turn per message), live through the event stream of its channel. */
export function useChat(channel: string, chat: ChatApi, onIdle?: () => void) {
  const [state, setState] = useState<MirelState | null>(null);
  const [activity, setActivity] = useState<string | null>(null);
  const idle = useRef(onIdle);
  idle.current = onIdle;
  const load = () => chat.state().then(setState);
  useEffect(() => {
    load();
    const source = new EventSource("/api/events");
    source.onmessage = (m) => {
      const e: TaskEvent = JSON.parse(m.data);
      if (!("channel" in e) || (e.channel ?? "mirel") !== channel) return;
      if (e.type === "chat") {
        setState((s) => (s ? { ...s, messages: [...s.messages.filter((x) => x.id !== e.message.id), e.message] } : s));
      } else if (e.type === "chat_tool") {
        setActivity(`${TOOLS[e.name] ?? e.name}…`);
      } else if (e.type === "chat_busy") {
        setActivity(e.busy ? "scrie…" : null);
        if (!e.busy) {
          load(); // questions answered in this turn disappear
          idle.current?.();
        }
      } else if (e.type === "chat_reset") {
        load();
      }
    };
    return () => source.close();
  }, [channel]);
  return { state, activity, setActivity };
}

/** The message list and the composer of a chat; `name` is who answers ("Mirel", "Ghidul"). */
export function ChatPanel({ channel, chat, name, intro, onIdle, insert }: {
  channel: string;
  chat: ChatApi;
  name: string;
  intro: string;
  onIdle?: () => void;
  insert?: { text: string; n: number }; // a new value appends its text to the composer (a picked question)
}) {
  const { state, activity, setActivity } = useChat(channel, chat, onIdle);
  const [text, setText] = useState("");
  useEffect(() => {
    if (insert) setText((t) => (t ? t + "\n" : "") + insert.text);
  }, [insert?.n]);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [state?.messages.length, activity]);
  if (!state) return <p className="muted">Se încarcă…</p>;
  const busy = state.busy || activity !== null;
  const send = (msg?: string) => {
    setError(null);
    setActivity("scrie…");
    chat.send(msg).then(
      () => setText(""),
      (e) => {
        setActivity(null);
        setError(String(e));
      },
    );
  };
  return (
    <div className="chat">
      <div className="messages">
        {!state.messages.length && !busy && (
          <div className="empty">
            <p className="muted">{intro}</p>
            <button className="primary" onClick={() => send()}>
              Începe
            </button>
          </div>
        )}
        {state.messages.map((m) => (
          <Message key={m.id} m={m} name={name} />
        ))}
        {activity && <div className="msg mirel typing">{name} {activity}</div>}
        <div ref={end} />
      </div>
      {error && <p className="error small">{error}</p>}
      <div className="composer">
        <textarea
          rows={2}
          value={text}
          placeholder={`Răspunde… (Enter trimite, Shift+Enter rând nou)`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (text.trim() && !busy) send(text);
            }
          }}
        />
        <button className="primary" disabled={!text.trim() || busy} onClick={() => send(text)}>
          Trimite
        </button>
      </div>
      <div className="actions">
        <button
          className="mini"
          disabled={busy || !state.messages.length}
          onClick={() => window.confirm("Începi o conversație nouă? Istoricul chatului se șterge (fișierele rămân).") &&
            chat.reset().catch((e) => setError(String(e)))}
        >
          Conversație nouă
        </button>
      </div>
    </div>
  );
}

const MIREL_CHAT: ChatApi = { state: api.mirel, send: api.mirelSend, reset: api.mirelReset };

/** Mirel: the chat for form questions the answers pool cannot answer. */
export function MirelTab({ onChanged }: { onChanged: () => void }) {
  const [state, setState] = useState<MirelState | null>(null);
  const [insert, setInsert] = useState<{ text: string; n: number } | undefined>();
  const load = () => api.mirel().then(setState);
  useEffect(() => {
    load();
  }, []);
  return (
    <section className="mirel">
      <aside className="questions">
        <h3>Întrebări deschise ({state?.questions.length ?? "…"})</h3>
        {state && !state.questions.length && <p className="muted small">Nicio întrebare fără răspuns în formulare.</p>}
        <ul>
          {(state?.questions ?? []).map((q) => (
            <li key={q.label}>
              <button className="question" onClick={() => setInsert((i) => ({ text: `${q.label} → `, n: (i?.n ?? 0) + 1 }))}>
                {q.label} {q.required && <b>*</b>}
              </button>
              <div className="muted small">
                <span className="tag">{REASONS[q.reason] ?? q.reason}</span>
                {q.options && ` · opțiuni: ${q.options.slice(0, 6).join(" / ")}${q.options.length > 6 ? " …" : ""}`}
              </div>
              <div className="muted small">
                {q.applications.map((a) => [a.company, a.title].filter(Boolean).join(" – ") || a.application).join("; ")}
              </div>
            </li>
          ))}
        </ul>
      </aside>
      <ChatPanel
        channel="mirel"
        chat={MIREL_CHAT}
        name="Mirel"
        intro="Mirel te întreabă ce nu știe pool-ul de răspunsuri, salvează răspunsurile reutilizabile în ANSWERS_POOL.json și deblochează aplicările care așteptau."
        onIdle={() => {
          load();
          onChanged();
        }}
        insert={insert}
      />
    </section>
  );
}

function Message({ m, name }: { m: ChatMessage; name: string }) {
  return (
    <div className={`msg ${m.role}`}>
      {m.role === "mirel" && <span className="who">{name}</span>}
      <div className="text">{bold(m.text)}</div>
    </div>
  );
}

/** The assistant writes markdown; only **bold** and `code` are worth rendering in a chat bubble. */
export function bold(text: string) {
  return text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <b key={i}>{part.slice(2, -2)}</b>
    ) : part.startsWith("`") && part.endsWith("`") ? (
      <code key={i}>{part.slice(1, -1)}</code>
    ) : (
      part
    ),
  );
}
