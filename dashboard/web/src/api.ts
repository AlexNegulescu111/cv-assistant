export type Category = "applied" | "in_progress" | "needs_input";

export interface Overview {
  cards: Record<Category, number>;
  statuses: Record<string, number>;
  unscored: number;
  synced: number;
}

export interface Job {
  id: number | null;
  company: string | null;
  title: string;
  location?: string | null;
  status: string;
  reason?: string | null;
  url?: string | null;
  application?: string | null;
  match?: number | null;
  verdict?: string | null;
  role_family?: string | null;
  applied_at?: string;
  log_status?: string;
}

export interface PendingField {
  field_id: string;
  label: string;
  type: string;
  required: boolean;
  reason: string;
}

export interface JobDetail extends Job {
  description: string | null;
  apply_url: string | null;
  score: { scored_at: string; match: number; verdict: string; knockouts: string; review_flags: string } | null;
  events: { at: string; from_status: string | null; to_status: string; note: string | null }[];
  pending: PendingField[];
  fill_report: string | null;
  has_prefill: boolean;
}

export interface Skill {
  skill: string;
  category: string;
  times_requested: number;
  priority: string;
  status: string;
  last_seen: string;
  mini_project: string;
  resource: string;
  jobs?: string;
  variants: string[]; // other names grouped under this one (same skill)
}

export interface SkillsState {
  skills: Skill[]; // one row per group, most asked first (distinct ads)
  rows: number; // rows in demanded_skills_to_learn.csv
  ungrouped: number; // names the Claude grouping has not seen yet
}

export type Credentials = Record<string, boolean> & { env_overrides: string[] };

export type Workplace = "remote" | "hybrid" | "onsite";

export interface Profile {
  workplaces: Workplace[];
  preferred_work_mode: string;
  preferred_contract_type: string;
  notice_period: string;
  earliest_start_date: string;
  willing_to_relocate: string;
  salary: Record<string, string | number>;
  target_titles: string[];
  target_domain: string;
}

export interface ApplyState {
  submit: boolean;
  daily_cap: number | null; // null = no daily cap
  submitted_today: number;
  remaining: number | null;
  top_n: number;
  has_default_cv: boolean;
  candidates_left: number; // open jobs with verdict apply still to take; each run takes the next top_n
  apply_threshold: number | null;
}

export type TaskKind =
  | "auto_apply" | "plan" | "collect" | "score" | "resume" | "cv" | "competency" | "login" | "apply_link"
  | "cluster_skills" | "onboard_extract" | "onboard_write";

export interface Task {
  id: number;
  kind: TaskKind;
  title: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  step: number | null;
  steps: string[];
  result: string | null;
  log?: string;
  usage: { tokens: number; cost_usd: number | null; runs: number } | null; // Claude runs of this task
}

export interface UsageTotals {
  claude: { tokens: number; input: number; output: number; cache: number; cost_usd: number; runs: number };
  jev: { tokens: number; jobs: number };
}

export interface Usage {
  days: number;
  periods: { today: UsageTotals; week: UsageTotals; month: UsageTotals };
  series: { day: string; claude: number; jev: number }[];
  by_kind: { kind: string; runs: number; tokens: number; cost_usd: number; avg_tokens: number }[];
  top_tasks: { task_id: number; title: string | null; kind: string; day: string; tokens: number; cost_usd: number }[];
  averages: { jev_per_job: number | null };
}

export interface ChatMessage {
  id: number;
  role: "user" | "mirel" | "system";
  text: string;
  at: string;
}

export interface OpenQuestion {
  label: string;
  reason: "ask" | "unknown" | "no_option";
  type: string;
  options: string[] | null;
  required: boolean;
  applications: { application: string; field_id: string; job_id: number | null; company: string | null; title: string | null }[];
}

export interface MirelState {
  messages: ChatMessage[];
  questions: OpenQuestion[];
  busy: boolean;
}

export interface Notification {
  id: number;
  at: string;
  kind: "needs_input" | "run" | "cv" | "error";
  title: string;
  body: string | null;
  job_id: number | null;
  task_id: number | null;
  read: number;
}

export interface Notifications {
  notifications: Notification[];
  unread: number;
  toasts: boolean;
}

export interface Schedule {
  supported: boolean;
  enabled: boolean;
  time: string | null;
  dry_run: boolean;
}

export type TaskEvent =
  | { type: "notification"; notification: Notification }
  | { type: "notification_read" }
  | { type: "task"; task: Task }
  | { type: "line"; task_id: number; line: string }
  | { type: "chat"; message: ChatMessage; channel?: string }
  | { type: "chat_busy"; busy: boolean; channel?: string }
  | { type: "chat_tool"; name: string; channel?: string }
  | { type: "chat_reset"; channel?: string };

export interface Facts {
  name: string;
  headline: string;
  contact: Record<string, string>;
  roles: { title: string; employer: string; start: string; end: string; location: string; highlights?: string[] }[];
  education: { degree: string; institution: string; start: string; end: string }[];
  certifications: { name: string; provider: string; year: string }[];
  languages: { language: string; level: string }[];
  skills: { name: string; category: string; level: string; evidence: string }[];
  projects: { name: string; description: string; url: string; level: string }[];
  target_roles_guess?: string[];
  cv_languages?: string[];
  open_questions?: string[];
  confirmed?: boolean;
}

export interface OnboardingAnswers {
  contact: Record<string, string>;
  screening: Record<string, string>;
  profile: {
    workplaces: Workplace[];
    preferred_contract_type: string;
    notice_period: string;
    earliest_start_date: string;
    willing_to_relocate: string;
    target_titles: string[];
    salary: Record<string, string | number>;
  };
  voluntary: Record<string, string>;
  consents: Record<string, boolean | null>;
  cv_languages: string[];
  target_domain: string;
}

export interface Onboarding {
  steps: { cv: boolean; facts: boolean; confirmed: boolean; answers: boolean; enrichment: boolean; written: boolean };
  pool_exists: boolean;
  needs_onboarding: boolean;
  cv: { name: string | null; chars: number; text: boolean };
  facts: Facts | null;
  answers: OnboardingAnswers;
  levels: string[];
  cv_languages: string[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
const put = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  applyState: () => get<ApplyState>("/api/apply-state"),
  setTopN: (top_n: number) =>
    request<ApplyState>("/api/apply-state", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ top_n }),
    }),
  tasks: () => get<Task[]>("/api/tasks"),
  task: (id: number) => get<Task>(`/api/tasks/${id}`),
  startTask: (kind: TaskKind, opts: { job_id?: number; dry_run?: boolean; text?: string; source_task?: number } = {}) =>
    post<Task>("/api/tasks", { kind, ...opts }),
  cancelTask: (id: number) => post<Task>(`/api/tasks/${id}/cancel`),
  revertTask: (id: number) => post<{ message: string }>(`/api/tasks/${id}/revert`),
  notifications: () => get<Notifications>("/api/notifications"),
  markRead: (ids?: number[]) => post<Notifications>("/api/notifications/read", ids ? { ids } : {}),
  setToasts: (toasts: boolean) =>
    request<Notifications>("/api/notifications/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ toasts }),
    }),
  schedule: () => get<Schedule>("/api/schedule"),
  saveSchedule: (time: string, dry_run: boolean) =>
    request<Schedule>("/api/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ time, dry_run }),
    }),
  deleteSchedule: () => request<Schedule>("/api/schedule", { method: "DELETE" }),
  mirel: () => get<MirelState>("/api/mirel"),
  mirelSend: (text?: string) => post<{ ok: boolean }>("/api/mirel", text ? { text } : {}),
  mirelReset: () => post<{ ok: boolean }>("/api/mirel/reset"),
  profile: () => get<Profile>("/api/profile"),
  saveProfile: (changes: Partial<Profile>) =>
    request<Profile>("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    }),
  setCredential: (name: string, value: string) =>
    post<{ name: string; set: boolean; overridden_by_environment: boolean }>("/api/credentials", { name, value }),
  overview: () => get<Overview>("/api/overview"),
  jobs: (c: Category) => get<Job[]>(`/api/jobs?category=${c}`),
  job: (id: number) => get<JobDetail>(`/api/jobs/${id}`),
  skills: () => get<SkillsState>("/api/skills"),
  usage: (days = 30) => get<Usage>(`/api/usage?days=${days}`),
  onboarding: () => get<Onboarding>("/api/onboarding"),
  uploadCv: (filename: string, content: string, extra_text: string) =>
    post<Onboarding>("/api/onboarding/cv", { filename, content, extra_text }),
  saveFacts: (facts: Facts) => put<Onboarding>("/api/onboarding/facts", facts),
  saveAnswers: (answers: OnboardingAnswers) => put<Onboarding>("/api/onboarding/answers", answers),
  skipQuestions: () => post<Onboarding>("/api/onboarding/skip-questions"),
  guide: () => get<MirelState>("/api/guide"),
  guideSend: (text?: string) => post<{ ok: boolean }>("/api/guide", text ? { text } : {}),
  guideReset: () => post<{ ok: boolean }>("/api/guide/reset"),
  credentials: () => get<Credentials>("/api/credentials"),
};
