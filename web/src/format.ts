/* Pure formatting helpers shared by chat and panel. */
import type { RecipeSpec } from "./types.ts";

const MODEL_SHORT: Record<string, string> = {
  logistic: "logistic",
  knn: "knn",
  tree: "tree",
  decision_tree: "tree",
  rf: "rf",
  random_forest: "rf",
  gb: "gb",
  gradient_boosting: "gb",
  nb: "nb",
  naive_bayes: "nb",
  svm: "svm",
  svm_linear: "svm",
  linear_svm: "svm",
  mlp: "mlp",
};

export function shortModel(name: string): string {
  return MODEL_SHORT[name] ?? name;
}

function hyperSuffix(hyper: Record<string, string | number | boolean>): string {
  const keys = Object.keys(hyper);
  if (keys.length === 0) return "";
  return `(${keys.map((k) => `${k}=${String(hyper[k])}`).join(", ")})`;
}

/** `standardize + logistic`, `stack(logistic, rf → logistic)`, `soft-vote(logistic, knn)`. */
export function recipeName(spec: RecipeSpec | string | null | undefined): string {
  if (!spec) return "—";
  if (typeof spec === "string") return spec;
  const parts: string[] = [];
  if (spec.transform && spec.transform !== "none") parts.push(spec.transform);
  const members = (spec.members ?? []).map(shortModel);
  const ens = spec.ensemble && spec.ensemble !== "none" ? spec.ensemble : null;
  if (ens) {
    const meta = spec.model ? shortModel(spec.model) : null;
    if (ens === "stacking" || ens === "stack") {
      parts.push(`stack(${members.join(", ")}${meta ? ` → ${meta}` : ""})`);
    } else if (ens === "bagging") {
      parts.push(`bagging(${meta ?? members.join(", ")})`);
    } else {
      const label = ens === "soft_voting" || ens === "voting" ? "soft-vote" : ens;
      parts.push(`${label}(${members.join(", ")})`);
    }
  } else {
    parts.push(`${shortModel(spec.model)}${hyperSuffix(spec.hyper ?? {})}`);
  }
  return parts.join(" + ");
}

export function fmtMetric(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function fmtDelta(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "±";
  return `${sign}${Math.abs(v).toFixed(digits)}`;
}

export function fmtMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function fmtClock(epochSec: number): string {
  const d = new Date(epochSec * 1000);
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

/** rakazo's roster time: clock today, "Yesterday", weekday within a week, else date. */
export function fmtRosterTime(updated: string | number | null | undefined): string {
  if (updated === null || updated === undefined || updated === "") return "";
  const d = typeof updated === "number" ? new Date(updated * 1000) : new Date(updated);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = (a: Date, b: Date) =>
    a.getDate() === b.getDate() && a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear();
  if (sameDay(d, now)) return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (sameDay(d, y)) return "Yesterday";
  const diff = now.getTime() - d.getTime();
  if (diff < 6 * 86_400_000) return d.toLocaleDateString("en-US", { weekday: "long" });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** Pick a stable avatar colour for a lab id. */
export const AVATAR_COLORS = ["#3B82F6", "#F97316", "#8B5CF6", "#22C55E", "#EC4899", "#14B8A6", "#EAB308"];
export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
export function colorFor(id: string, explicit?: string): string {
  if (explicit) return explicit;
  return AVATAR_COLORS[hashString(id) % AVATAR_COLORS.length] ?? "#3B82F6";
}
