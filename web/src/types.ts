/* Event union (SPEC §2) and HTTP shapes (SPEC §2 HTTP API). Every field the UI
   reads is declared here; nothing in the app touches an event as `any`. */

export type Gate = "promote" | "keep" | "discard" | "explore_only" | "repeat" | "crash";
export type Lane = "explore" | "confirm" | "repair";
export type RunnerKind = "local" | "daytona";
export type StagnationGrade =
  | "local_optimum"
  | "family_tax"
  | "superadditive_harm"
  | "measurement_resolution"
  | "frozen_axis"
  | "information_gain";

export interface RecipeSpec {
  transform: string;
  model: string;
  ensemble: string | null;
  hyper: Record<string, string | number | boolean>;
  members: string[] | null;
}

/** `catalog_summary` on lab_started: families → factor names. `n_factors` is
 *  optional; the UI derives it from the families when absent. */
export interface CatalogSummary {
  families: Record<string, string[]>;
  n_factors?: number;
  dataset?: string;
}

/** Inline card the engine may attach to an agent_message. `mark` accepts the
 *  literal glyph or its name. */
export type CardMark = "ok" | "no" | "seal" | "star" | "info" | "✓" | "✗" | "⊘" | "★" | "○";
export interface MessageCard {
  mark: CardMark;
  label: string;
  text: string;
  metric?: string;
}

interface Base {
  /** Optional epoch seconds set by the engine; the UI stamps receive-time when absent. */
  ts?: number;
}

export interface LabStartedEvent extends Base {
  type: "lab_started";
  lab_id: string;
  /** The engine sends {dataset, desc, n, d, prompt}; the scripted demo sends a string. */
  task: string | { dataset?: string; desc?: string; prompt?: string; n?: number; d?: number };
  metric: string;
  /** The engine's summary is {factors, groups, by_group}; state.ts normalises both shapes. */
  catalog_summary: CatalogSummary | { factors?: number; groups?: Record<string, number>; by_group: Record<string, string[]> };
  providers: string[] | Record<string, unknown>;
  budget: number | Record<string, unknown>;
}
export interface HypothesisEvent extends Base {
  type: "hypothesis";
  id: string;
  text: string;
  kind: "factor" | "recipe";
  family: string;
  control: string;
  treatment: string;
  cost_est: number;
  why: string;
}
export interface ExperimentStartedEvent extends Base {
  type: "experiment_started";
  id: string;
  hypothesis_id: string;
  lane: Lane;
  spec: RecipeSpec;
  runner: RunnerKind;
  sandbox_id?: string | null;
}
export interface ExperimentResultEvent extends Base {
  type: "experiment_result";
  id: string;
  metric: number;
  std: number;
  delta_vs_control: number | null;
  cost_ms: number;
  gate: Gate;
  reason: string;
}
export interface FamilySealedEvent extends Base {
  type: "family_sealed";
  family: string;
  evidence: { spec: RecipeSpec | string; delta: number }[];
  text: string;
}
export interface StagnationEvent extends Base {
  type: "stagnation";
  grade: StagnationGrade;
  explanations: [string, string];
  separating_observation: string;
  next: string;
}
export interface ChampionEvent extends Base {
  type: "champion";
  spec: RecipeSpec;
  metric: number;
  delta_vs_best_single: number;
  evals_used: number;
  evals_grid_equivalent: number;
}
export interface AskOption {
  id: string;
  label: string;
  detail?: string;
}
export interface AskEvent extends Base {
  type: "ask";
  id: string;
  question: string;
  options: AskOption[];
  reason: string;
}
export interface AgentMessageEvent extends Base {
  type: "agent_message";
  text: string;
  cards?: MessageCard[];
}
export interface ProgressPoint {
  round: number;
  best: number;
  mean: number;
  worst: number;
}
export interface ProgressEvent extends Base {
  type: "progress";
  evals_done: number;
  evals_grid: number;
  best_metric: number;
  best_single_metric: number;
  history: ProgressPoint[];
}
export interface DoneEvent extends Base {
  type: "done";
  summary: string;
}
/** Echo of a steering message. Not in SPEC §2; the UI also adds these locally
 *  after POST /steer, so the engine may emit it for replay fidelity or not. */
export interface UserMessageEvent extends Base {
  type: "user_message";
  text: string;
}

export type AirlockEvent =
  | LabStartedEvent
  | HypothesisEvent
  | ExperimentStartedEvent
  | ExperimentResultEvent
  | FamilySealedEvent
  | StagnationEvent
  | ChampionEvent
  | AskEvent
  | AgentMessageEvent
  | ProgressEvent
  | DoneEvent
  | UserMessageEvent;

export type EventType = AirlockEvent["type"];

export const EVENT_TYPES: readonly EventType[] = [
  "lab_started",
  "hypothesis",
  "experiment_started",
  "experiment_result",
  "family_sealed",
  "stagnation",
  "champion",
  "ask",
  "agent_message",
  "progress",
  "done",
  "user_message",
];

/* ---- HTTP ---- */

export type LabStatus = "running" | "waiting" | "done" | "error" | "idle" | string;

/** Row of GET /api/labs. `updated` is ISO-8601 or epoch seconds. */
export interface LabRow {
  id: string;
  title: string;
  last_message: string;
  updated: string | number;
  status: LabStatus;
  /** Optional avatar colour hint; the UI hashes `id` when absent. */
  color?: string;
}
