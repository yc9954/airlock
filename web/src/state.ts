/* Pure reducer: SSE events → what the chat and the right panel render.
   Chat gets the calm surface (bubbles + cards); raw experiment lifecycle stays
   in `experiments`/`running` for the panel. */
import { recipeName } from "./format.ts";
import type {
  AirlockEvent,
  AskEvent,
  CatalogSummary,
  ChampionEvent,
  ExperimentStartedEvent,
  FamilySealedEvent,
  Gate,
  LabStartedEvent,
  CardMark,
  HypothesisEvent,
  Lane,
  MessageCard,
  ProgressEvent,
  RecipeSpec,
  RunnerKind,
  StagnationEvent,
} from "./types.ts";

export interface ResultRow {
  id: string;
  name: string;
  metric: number;
  std: number;
  delta: number | null;
  gate: Gate;
  reason: string;
  runner: RunnerKind | null;
  cost_ms: number;
}

export type ChatItem =
  | { kind: "divider"; id: string; ts: number }
  | { kind: "agent"; id: string; ts: number; text: string; cards?: MessageCard[] }
  | { kind: "user"; id: string; ts: number; text: string }
  | { kind: "hypothesis"; id: string; ts: number; hyp: HypothesisEvent }
  | { kind: "launch"; id: string; ts: number; runner: RunnerKind; lane: Lane; ids: string[] }
  | { kind: "results"; id: string; ts: number; rows: ResultRow[] }
  | { kind: "seal"; id: string; ts: number; ev: FamilySealedEvent }
  | { kind: "stagnation"; id: string; ts: number; ev: StagnationEvent }
  | { kind: "champion"; id: string; ts: number; ev: ChampionEvent }
  | { kind: "ask"; id: string; ts: number; ev: AskEvent }
  | { kind: "done"; id: string; ts: number; summary: string };

export interface Experiment {
  id: string;
  hypothesisId: string;
  lane: Lane;
  spec: RecipeSpec;
  runner: RunnerKind;
  sandboxId: string | null;
  startedAt: number; // ms epoch (receive time)
  result: ResultRow | null;
}

export type LabStatus = "idle" | "running" | "waiting" | "done";

export interface LabState {
  labId: string | null;
  task: string;
  metric: string;
  catalog: CatalogSummary | null;
  providers: string[];
  status: LabStatus;
  items: ChatItem[];
  hypotheses: Record<string, HypothesisEvent>;
  experiments: Record<string, Experiment>;
  running: string[];
  recent: string[]; // last finished experiment ids, newest first
  sealed: string[];
  champion: ChampionEvent | null;
  progress: ProgressEvent | null;
  asks: Record<string, { ev: AskEvent; answered: string | null }>;
  openAskId: string | null;
  evalsDone: number;
  harms: ResultRow[]; // composites shown to hurt
  lastMessage: string;
  lastDividerTs: number;
  seq: number;
}

export const DIVIDER_GAP_SEC = 10 * 60;

export function initialState(): LabState {
  return {
    labId: null,
    task: "",
    metric: "",
    catalog: null,
    providers: [],
    status: "idle",
    items: [],
    hypotheses: {},
    experiments: {},
    running: [],
    recent: [],
    sealed: [],
    champion: null,
    progress: null,
    asks: {},
    openAskId: null,
    evalsDone: 0,
    harms: [],
    lastMessage: "",
    lastDividerTs: 0,
    seq: 0,
  };
}

export type Action =
  | { type: "event"; event: AirlockEvent; receivedAt: number } // receivedAt: ms epoch
  | { type: "user"; text: string; ts: number } // ts: seconds
  | { type: "answered"; askId: string; optionId: string }
  | { type: "reset" };

function push(state: LabState, item: ChatItem): LabState {
  const items = state.items.slice();
  let lastDividerTs = state.lastDividerTs;
  if (item.kind !== "divider" && item.ts - lastDividerTs >= DIVIDER_GAP_SEC) {
    items.push({ kind: "divider", id: `d${state.seq}`, ts: item.ts });
    lastDividerTs = item.ts;
  }
  items.push(item);
  return { ...state, items, lastDividerTs, seq: state.seq + 1 };
}

function eventTs(ev: AirlockEvent, receivedAt: number): number {
  return typeof ev.ts === "number" ? ev.ts : Math.floor(receivedAt / 1000);
}

/** Flatten `lab_started.providers` (an array, or a {role: label} map) into
 *  searchable strings: both the labels ("daytona", "Nosana") and "role:label". */
function providersOf(p: unknown): string[] {
  if (Array.isArray(p)) return p.map(String);
  if (p && typeof p === "object") {
    return Object.entries(p as Record<string, unknown>).flatMap(([k, v]) => [String(v), `${k}:${String(v)}`]);
  }
  return [];
}

/** The engine sends `task` as {dataset, desc, n, d, prompt}; the mock sends a string. */
function taskLabel(t: LabStartedEvent["task"]): string {
  if (typeof t === "string") return t;
  if (t && typeof t === "object") return t.desc || t.dataset || t.prompt || "";
  return "";
}

/** The engine's catalog summary is {factors, groups, by_group}; the UI wants {families, n_factors}. */
function catalogOf(c: LabStartedEvent["catalog_summary"]): CatalogSummary | null {
  if (!c || typeof c !== "object") return null;
  // The engine also reports `families` as a count, so `by_group` (group → factor names) wins.
  if ("by_group" in c && c.by_group && typeof c.by_group === "object") return { families: c.by_group, n_factors: c.factors };
  const fam = (c as { families?: unknown }).families;
  if (fam && typeof fam === "object" && Object.values(fam as Record<string, unknown>).every(Array.isArray)) return c as CatalogSummary;
  return null;
}

/** The engine attaches cards as {kind, title, detail} or {kind: "checklist", title, rows[]};
 *  the scripted demo already sends {mark, label, text}. Both become CardLine props. */
const GLYPHS = new Set(["✓", "✗", "⊘", "★", "○"]);
function kindMark(kind: string): CardMark {
  if (kind === "promote" || kind === "keep") return "ok";
  if (kind === "champion") return "star";
  if (kind === "sealed") return "seal";
  if (kind === "discard" || kind === "discarded" || kind === "crash" || kind === "harm") return "no";
  return "info";
}
function normalizeCards(raw: unknown): MessageCard[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: MessageCard[] = [];
  for (const c of raw as Record<string, unknown>[]) {
    if (!c || typeof c !== "object") continue;
    if (typeof c.text === "string" && "mark" in c) {
      out.push({ mark: c.mark as CardMark, label: String(c.label ?? ""), text: c.text, metric: typeof c.metric === "string" ? c.metric : undefined });
      continue;
    }
    const kind = String(c.kind ?? "info");
    const title = typeof c.title === "string" ? c.title : "";
    if (kind === "checklist" && Array.isArray(c.rows)) {
      if (title) out.push({ mark: "info", label: "", text: title });
      for (const r of c.rows as Record<string, unknown>[]) {
        const glyph = typeof r.mark === "string" && GLYPHS.has(r.mark) ? (r.mark as CardMark) : null;
        const gate = typeof r.gate === "string" ? r.gate : "";
        const mark: CardMark = glyph ?? (gate === "explore_only" ? "○" : r.ok ? "ok" : "no");
        out.push({ mark, label: gate.replace(/_/g, " "), text: String(r.text ?? "") });
      }
      continue;
    }
    const detail = typeof c.detail === "string" && c.detail ? ` — ${c.detail}` : "";
    out.push({ mark: kindMark(kind), label: kind === "discarded" ? "discard" : kind, text: title + detail });
  }
  return out.length ? out : undefined;
}

/** Label of the open-model layer (e.g. "Nosana"), or null when narration is template-only. */
export function llmProvider(state: LabState): string | null {
  const hit = state.providers.find((p) => p.startsWith("llm:"));
  if (!hit) return null;
  const label = hit.slice(4);
  return label && label !== "templates" && label !== "none" ? label : null;
}

export function isHarm(row: ResultRow): boolean {
  return /superadditive|harm/i.test(row.reason);
}

export function reduce(state: LabState, action: Action): LabState {
  switch (action.type) {
    case "reset":
      return initialState();
    case "user": {
      const s = push(state, { kind: "user", id: `u${state.seq}`, ts: action.ts, text: action.text });
      return { ...s, lastMessage: action.text };
    }
    case "answered": {
      const ask = state.asks[action.askId];
      if (!ask) return state;
      return {
        ...state,
        asks: { ...state.asks, [action.askId]: { ...ask, answered: action.optionId } },
        openAskId: state.openAskId === action.askId ? null : state.openAskId,
        status: state.status === "waiting" ? "running" : state.status,
      };
    }
    case "event":
      return applyEvent(state, action.event, action.receivedAt);
  }
}

function applyEvent(state: LabState, ev: AirlockEvent, receivedAt: number): LabState {
  const ts = eventTs(ev, receivedAt);
  // Any event other than progress means the engine has moved past an open ask.
  let s: LabState = state;
  if (ev.type !== "progress" && ev.type !== "ask" && state.openAskId) {
    s = { ...s, openAskId: null, status: s.status === "waiting" ? "running" : s.status };
  }
  switch (ev.type) {
    case "lab_started":
      return {
        ...s,
        labId: ev.lab_id,
        task: taskLabel(ev.task),
        metric: ev.metric,
        catalog: catalogOf(ev.catalog_summary),
        providers: providersOf(ev.providers),
        status: "running",
      };
    case "agent_message": {
      const next = push(s, { kind: "agent", id: `a${s.seq}`, ts, text: ev.text ?? "", cards: normalizeCards(ev.cards) });
      return { ...next, lastMessage: ev.text, status: next.status === "idle" ? "running" : next.status };
    }
    case "user_message":
      return push(s, { kind: "user", id: `u${s.seq}`, ts, text: ev.text });
    case "hypothesis": {
      const next = push(s, { kind: "hypothesis", id: `h${s.seq}`, ts, hyp: ev });
      return { ...next, hypotheses: { ...next.hypotheses, [ev.id]: ev } };
    }
    case "experiment_started":
      return startExperiment(s, ev, ts, receivedAt);
    case "experiment_result": {
      const exp = s.experiments[ev.id];
      const row: ResultRow = {
        id: ev.id,
        name: recipeName(exp?.spec),
        metric: ev.metric,
        std: ev.std,
        delta: ev.delta_vs_control,
        gate: ev.gate,
        reason: ev.reason,
        runner: exp?.runner ?? null,
        cost_ms: ev.cost_ms,
      };
      // The engine learns which sandbox ran the spec only when the result comes back.
      const rt = (ev as { runtime?: { sandbox?: string; sandbox_id?: string | null } }).runtime;
      const sandboxId = rt?.sandbox_id ?? exp?.sandboxId ?? null;
      const runner: RunnerKind = rt?.sandbox === "daytona" ? "daytona" : (exp?.runner ?? "local");
      const experiments = exp
        ? { ...s.experiments, [ev.id]: { ...exp, result: { ...row, runner }, sandboxId, runner } }
        : s.experiments;
      const running = s.running.filter((id) => id !== ev.id);
      const recent = [ev.id, ...s.recent.filter((id) => id !== ev.id)].slice(0, 8);
      const harms = isHarm(row) ? [...s.harms, row] : s.harms;
      const evalsDone = ev.gate === "crash" ? s.evalsDone : s.evalsDone + 1;
      const base = { ...s, experiments, running, recent, harms, evalsDone };
      const last = base.items[base.items.length - 1];
      if (last && last.kind === "results") {
        const items = base.items.slice(0, -1);
        items.push({ ...last, rows: [...last.rows, row] });
        return { ...base, items, lastMessage: rowSummary(row) };
      }
      const next = push(base, { kind: "results", id: `r${base.seq}`, ts, rows: [row] });
      return { ...next, lastMessage: rowSummary(row) };
    }
    case "family_sealed": {
      const next = push(s, { kind: "seal", id: `s${s.seq}`, ts, ev });
      return {
        ...next,
        sealed: next.sealed.includes(ev.family) ? next.sealed : [...next.sealed, ev.family],
        lastMessage: ev.text,
      };
    }
    case "stagnation": {
      const next = push(s, { kind: "stagnation", id: `g${s.seq}`, ts, ev });
      return { ...next, lastMessage: `Stalled — ${ev.grade.replace(/_/g, " ")}` };
    }
    case "champion": {
      const next = push(s, { kind: "champion", id: `c${s.seq}`, ts, ev });
      return { ...next, champion: ev, lastMessage: `★ Champion → ${recipeName(ev.spec)} ${ev.metric.toFixed(3)}` };
    }
    case "ask": {
      const next = push(s, { kind: "ask", id: `k${s.seq}`, ts, ev });
      return {
        ...next,
        asks: { ...next.asks, [ev.id]: { ev, answered: null } },
        openAskId: ev.id,
        status: "waiting",
        lastMessage: ev.question,
      };
    }
    case "progress":
      return { ...s, progress: ev };
    case "done": {
      const next = push(s, { kind: "done", id: `z${s.seq}`, ts, summary: ev.summary });
      return { ...next, status: "done", running: [], openAskId: null };
    }
  }
}

function startExperiment(
  s: LabState,
  ev: ExperimentStartedEvent,
  ts: number,
  receivedAt: number,
): LabState {
  const exp: Experiment = {
    id: ev.id,
    hypothesisId: ev.hypothesis_id,
    lane: ev.lane,
    spec: ev.spec,
    runner: ev.runner,
    sandboxId: ev.sandbox_id ?? null,
    startedAt: receivedAt,
    result: null,
  };
  const base: LabState = {
    ...s,
    experiments: { ...s.experiments, [ev.id]: exp },
    running: s.running.includes(ev.id) ? s.running : [...s.running, ev.id],
    status: s.status === "idle" ? "running" : s.status,
  };
  const last = base.items[base.items.length - 1];
  if (last && last.kind === "launch" && last.runner === ev.runner && last.lane === ev.lane) {
    const items = base.items.slice(0, -1);
    items.push({ ...last, ids: [...last.ids, ev.id] });
    return { ...base, items };
  }
  return push(base, { kind: "launch", id: `l${base.seq}`, ts, runner: ev.runner, lane: ev.lane, ids: [ev.id] });
}

function rowSummary(row: ResultRow): string {
  const mark = gateMark(row.gate);
  return `${mark} ${gateLabel(row.gate)} → ${row.name} ${row.metric.toFixed(3)}`;
}

export function gateMark(gate: Gate): "✓" | "✗" | "○" | "↻" | "!" {
  switch (gate) {
    case "promote":
    case "keep":
      return "✓";
    case "discard":
      return "✗";
    case "explore_only":
      return "○";
    case "repeat":
      return "↻";
    case "crash":
      return "!";
  }
}

export function gateLabel(gate: Gate): string {
  switch (gate) {
    case "promote":
      return "Promoted";
    case "keep":
      return "Kept";
    case "discard":
      return "Discarded";
    case "explore_only":
      return "Screened";
    case "repeat":
      return "Repeat";
    case "crash":
      return "Crashed";
  }
}

/** Sandboxes/workers currently busy, for the panel. */
export function runningExperiments(state: LabState): Experiment[] {
  return state.running.map((id) => state.experiments[id]).filter((e): e is Experiment => Boolean(e));
}

export function factorCount(catalog: CatalogSummary | null): number {
  if (!catalog) return 0;
  if (typeof catalog.n_factors === "number") return catalog.n_factors;
  return Object.values(catalog.families).reduce((n, f) => n + f.length, 0);
}
