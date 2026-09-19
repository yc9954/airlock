/* Canned event streams for ?mock=1. Timings are real seconds from stream open;
   `ts` is simulated wall-clock so the chat shows rakazo-style time dividers.
   The breast_cancer lab is the demo: explore screen → confirmations fan out to
   Daytona → polynomial family sealed → hyper round → stagnation critique →
   soft-vote falsifier → superadditive-harm card → ask → stacking round →
   ★ composite champion in 28 evals (grid: 180). */
import type { AirlockEvent, CatalogSummary, Gate, RecipeSpec } from "../types.ts";

export interface TimedEvent {
  t: number; // seconds after open
  event: AirlockEvent;
}

/** Real seconds per timeline second — stretches the demo to ~3 minutes. */
export const MOCK_DURATION_SCALE = 1.4;

function todayAt(h: number, m: number): number {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return Math.floor(d.getTime() / 1000);
}
const BASE_TS = todayAt(12, 4);
/** 1 timeline second = 20 simulated seconds, so dividers appear every ~30 s. */
const simTs = (t: number) => BASE_TS + Math.floor(t * 20);

export const MOCK_CATALOG: CatalogSummary = {
  dataset: "breast_cancer",
  n_factors: 31,
  families: {
    transform: ["none", "standardize", "polynomial(2)", "pca(8)", "select_k_best(12)", "quantile"],
    model: ["logistic", "knn", "tree", "rf", "gb", "nb", "svm", "mlp"],
    ensemble: ["none", "soft_voting", "stacking", "bagging"],
    hyper: [
      "logistic.C",
      "knn.k",
      "knn.weights",
      "tree.max_depth",
      "tree.min_leaf",
      "rf.max_depth",
      "rf.n_estimators",
      "gb.learning_rate",
      "gb.n_estimators",
      "svm.C",
      "mlp.hidden",
      "mlp.alpha",
      "nb.var_smoothing",
    ],
  },
};

export function spec(
  model: string,
  transform = "none",
  hyper: Record<string, string | number | boolean> = {},
  ensemble: string | null = null,
  members: string[] | null = null,
): RecipeSpec {
  return { transform, model, ensemble, hyper, members };
}

const SB = ["sb-7f3a2c", "sb-9b41de", "sb-c02e77", "sb-3dd9a1", "sb-e81b06", "sb-52af90"] as const;

const at = (t: number, ev: AirlockEvent): TimedEvent => ({ t, event: { ...ev, ts: simTs(t) } });

function start(
  t: number,
  id: string,
  hypothesis_id: string,
  lane: "explore" | "confirm" | "repair",
  s: RecipeSpec,
  runner: "local" | "daytona",
  sandbox_id: string | null = null,
): TimedEvent {
  return at(t, { type: "experiment_started", id, hypothesis_id, lane, spec: s, runner, sandbox_id });
}

function result(
  t: number,
  id: string,
  metric: number,
  std: number,
  delta: number | null,
  gate: Gate,
  reason: string,
  cost_ms: number,
): TimedEvent {
  return at(t, { type: "experiment_result", id, metric, std, delta_vs_control: delta, cost_ms, gate, reason });
}

const STACK_LR = spec("logistic", "standardize", {}, "stacking", ["logistic", "rf"]);

export const MOCK_BC_EVENTS: TimedEvent[] = [
  at(0, {
    type: "lab_started",
    lab_id: "lab_bc",
    task: "breast_cancer",
    metric: "5-fold accuracy",
    catalog_summary: MOCK_CATALOG,
    providers: ["local", "daytona"],
    budget: 60,
  }),
  at(0.5, {
    type: "agent_message",
    text:
      "Starting on `breast_cancer` · metric: 5-fold accuracy · catalog: 31 factors in 4 families. Promote bar: +0.003 over control, falsifier declared per axis. Grid over the same catalog: 180 evals.",
  }),
  at(2, {
    type: "agent_message",
    text: "Explore lane first: screen the 8 base models at default hyper, no transform. 8 evals on the local pool — cheap, candidates only.",
  }),
  start(3, "e1", "h0", "explore", spec("logistic"), "local"),
  start(3.1, "e2", "h0", "explore", spec("knn"), "local"),
  start(3.2, "e3", "h0", "explore", spec("tree"), "local"),
  start(3.3, "e4", "h0", "explore", spec("rf"), "local"),
  start(3.4, "e5", "h0", "explore", spec("gb"), "local"),
  start(3.5, "e6", "h0", "explore", spec("nb"), "local"),
  start(3.6, "e7", "h0", "explore", spec("svm"), "local"),
  start(3.7, "e8", "h0", "explore", spec("mlp"), "local"),
  result(5, "e1", 0.947, 0.012, null, "explore_only", "screen", 1840),
  result(6, "e2", 0.929, 0.015, null, "explore_only", "screen", 620),
  result(7, "e3", 0.912, 0.021, null, "explore_only", "screen", 410),
  result(8, "e4", 0.958, 0.011, null, "explore_only", "screen", 3120),
  result(9, "e5", 0.954, 0.013, null, "explore_only", "screen", 4370),
  result(10, "e6", 0.936, 0.014, null, "explore_only", "screen", 280),
  result(11, "e7", 0.949, 0.012, null, "explore_only", "screen", 950),
  result(12, "e8", 0.941, 0.017, null, "explore_only", "screen", 5210),
  at(13, {
    type: "progress",
    evals_done: 8,
    evals_grid: 180,
    best_metric: 0.958,
    best_single_metric: 0.958,
    history: [{ round: 1, best: 0.958, mean: 0.9408, worst: 0.912 }],
  }),
  at(14, {
    type: "agent_message",
    text:
      "Best single: random forest 0.958. Leading explanation: the linear-ish models are starved by feature scale — `standardize` should move logistic, svm, knn and mlp. Confirm lane: one factor per axis, control vs treatment, same folds.",
  }),
  at(15, {
    type: "hypothesis",
    id: "h1",
    text: "`standardize` lifts logistic by ≥ 0.003",
    kind: "factor",
    family: "transform",
    control: "logistic",
    treatment: "standardize + logistic",
    cost_est: 1,
    why: "Unscaled features; lbfgs hits max_iter at 0.947. Falsifier: treatment < 0.950.",
  }),
  at(16, {
    type: "hypothesis",
    id: "h2",
    text: "`polynomial(2)` adds usable interactions for logistic",
    kind: "factor",
    family: "transform",
    control: "logistic",
    treatment: "polynomial(2) + logistic",
    cost_est: 1,
    why: "30 → 495 features; interaction terms may carry signal. Falsifier: treatment ≤ control.",
  }),
  at(17, {
    type: "hypothesis",
    id: "h3",
    text: "`standardize` fixes knn's distance metric",
    kind: "factor",
    family: "transform",
    control: "knn",
    treatment: "standardize + knn",
    cost_est: 1,
    why: "Distances are dominated by area/perimeter scale. Falsifier: treatment < 0.932.",
  }),
  at(18, {
    type: "hypothesis",
    id: "h4",
    text: "`pca(8)` removes collinearity without losing signal",
    kind: "factor",
    family: "transform",
    control: "logistic",
    treatment: "pca(8) + logistic",
    cost_est: 1,
    why: "Features are highly collinear; 8 components carry 95% of variance. Falsifier: treatment ≤ control.",
  }),
  at(19, { type: "agent_message", text: "Fanning out 4 confirmations to Daytona — 4 sandboxes, ~6 s each." }),
  start(20, "e9", "h1", "confirm", spec("logistic", "standardize"), "daytona", SB[0]),
  start(20.2, "e10", "h2", "confirm", spec("logistic", "polynomial(2)"), "daytona", SB[1]),
  start(20.4, "e11", "h3", "confirm", spec("knn", "standardize"), "daytona", SB[2]),
  start(20.6, "e12", "h4", "confirm", spec("logistic", "pca(8)"), "daytona", SB[3]),
  result(26, "e9", 0.951, 0.01, 0.004, "keep", "beats control 0.947 by 0.004 ≥ min_delta; falsifier not triggered", 5920),
  result(28, "e10", 0.938, 0.016, -0.009, "discard", "below control; 495 features overfit 455 rows", 7480),
  result(30, "e11", 0.947, 0.013, 0.018, "keep", "knn 0.929 → 0.947 once distances are scaled", 5110),
  result(32, "e12", 0.944, 0.012, -0.003, "discard", "8 components lose separating variance", 5630),
  at(33, {
    type: "progress",
    evals_done: 12,
    evals_grid: 180,
    best_metric: 0.958,
    best_single_metric: 0.958,
    history: [
      { round: 1, best: 0.958, mean: 0.9408, worst: 0.912 },
      { round: 2, best: 0.958, mean: 0.945, worst: 0.938 },
    ],
  }),
  at(34, {
    type: "agent_message",
    text:
      "Kept `standardize` for logistic and knn. `polynomial(2)` hurt logistic — before I close the family it gets a pair and a triple: a recipe miss doesn't close its isolated factors.",
  }),
  at(35, {
    type: "hypothesis",
    id: "h5",
    text: "`polynomial(2)` helps svm even if it hurt logistic",
    kind: "factor",
    family: "transform",
    control: "svm",
    treatment: "polynomial(2) + svm",
    cost_est: 1,
    why: "A max-margin learner may tolerate the feature blow-up. Falsifier: treatment ≤ 0.949.",
  }),
  at(36, {
    type: "hypothesis",
    id: "h6",
    text: "pair: `standardize` + `polynomial(2)` + logistic",
    kind: "recipe",
    family: "transform",
    control: "standardize + logistic",
    treatment: "standardize + polynomial(2) + logistic",
    cost_est: 1,
    why: "Scaling first may tame the interaction terms. Falsifier: treatment < 0.954.",
  }),
  at(37, {
    type: "hypothesis",
    id: "h7",
    text: "triple: `polynomial(2)` + `pca(8)` + logistic",
    kind: "recipe",
    family: "transform",
    control: "standardize + logistic",
    treatment: "polynomial(2) + pca(8) + logistic",
    cost_est: 1,
    why: "Compressing the 495 interactions back to 8 could keep signal, drop noise.",
  }),
  start(38, "e13", "h5", "confirm", spec("svm", "polynomial(2)"), "local"),
  start(38.2, "e14", "h6", "confirm", spec("logistic", "standardize+polynomial(2)"), "local"),
  start(38.4, "e15", "h7", "confirm", spec("logistic", "polynomial(2)+pca(8)"), "local"),
  result(43, "e13", 0.943, 0.014, -0.006, "discard", "below control 0.949", 6810),
  result(45, "e14", 0.947, 0.012, -0.004, "discard", "below control 0.951; scaling doesn't save it", 7020),
  result(47, "e15", 0.94, 0.015, -0.011, "discard", "compression keeps the noise", 7350),
  at(48, {
    type: "family_sealed",
    family: "polynomial",
    evidence: [
      { spec: spec("logistic", "polynomial(2)"), delta: -0.009 },
      { spec: spec("svm", "polynomial(2)"), delta: -0.006 },
      { spec: spec("logistic", "standardize+polynomial(2)"), delta: -0.004 },
      { spec: spec("logistic", "polynomial(2)+pca(8)"), delta: -0.011 },
    ],
    text: "Sealed family `polynomial` — isolated, pair, triple all below the bar. Won't recombine.",
  }),
  at(49, {
    type: "progress",
    evals_done: 15,
    evals_grid: 180,
    best_metric: 0.958,
    best_single_metric: 0.958,
    history: [
      { round: 1, best: 0.958, mean: 0.9408, worst: 0.912 },
      { round: 2, best: 0.958, mean: 0.945, worst: 0.938 },
      { round: 3, best: 0.958, mean: 0.9433, worst: 0.94 },
    ],
  }),
  at(50, {
    type: "agent_message",
    text: "Hyper family next: one level each way on the two strongest trees, plus C on svm under standardize.",
  }),
  at(51, {
    type: "hypothesis",
    id: "h8",
    text: "rf is depth-limited",
    kind: "factor",
    family: "hyper",
    control: "rf",
    treatment: "rf(max_depth=8)",
    cost_est: 1,
    why: "Default depth is unbounded on 455 rows; a cap may regularise. Falsifier: no change.",
  }),
  at(52, {
    type: "hypothesis",
    id: "h9",
    text: "gb is learning too fast",
    kind: "factor",
    family: "hyper",
    control: "gb",
    treatment: "gb(learning_rate=0.05)",
    cost_est: 1,
    why: "lr 0.1 with 100 trees; halving it may trade bias for variance. Falsifier: Δ < 0.003.",
  }),
  at(53, {
    type: "hypothesis",
    id: "h10",
    text: "svm wants a softer margin once scaled",
    kind: "factor",
    family: "hyper",
    control: "svm",
    treatment: "standardize + svm(C=0.3)",
    cost_est: 1,
    why: "After scaling, C=1 over-fits the few outliers. Falsifier: treatment ≤ 0.949.",
  }),
  start(54, "e16", "h8", "confirm", spec("rf", "none", { max_depth: 8 }), "daytona", SB[0]),
  start(54.2, "e17", "h9", "confirm", spec("gb", "none", { learning_rate: 0.05 }), "daytona", SB[1]),
  start(54.4, "e18", "h10", "confirm", spec("svm", "standardize", { C: 0.3 }), "daytona", SB[2]),
  result(60, "e16", 0.958, 0.011, 0, "discard", "no change; depth isn't the limiter", 6240),
  result(62, "e17", 0.956, 0.013, 0.002, "discard", "below the bar (0.003) — noise-sized", 8130),
  result(64, "e18", 0.956, 0.011, 0.007, "keep", "svm 0.949 → 0.956 under standardize", 5480),
  at(65, {
    type: "progress",
    evals_done: 18,
    evals_grid: 180,
    best_metric: 0.958,
    best_single_metric: 0.958,
    history: [
      { round: 1, best: 0.958, mean: 0.9408, worst: 0.912 },
      { round: 2, best: 0.958, mean: 0.945, worst: 0.938 },
      { round: 3, best: 0.958, mean: 0.9433, worst: 0.94 },
      { round: 4, best: 0.958, mean: 0.9567, worst: 0.956 },
    ],
  }),
  at(66, {
    type: "stagnation",
    grade: "local_optimum",
    explanations: [
      "Local optimum on single models: every factor bolted onto one model lands within ±0.003 of rf 0.958.",
      "Ensembles untested: the kept components are diverse (linear, distance, trees) — their errors may be uncorrelated.",
    ],
    separating_observation:
      "A soft-vote of two kept models beats rf by ≥ 0.003 → ensembles are alive (B). Lands within noise → local optimum (A).",
    next: "soft-vote(logistic, knn) under standardize — 2 evals",
  }),
  at(67, {
    type: "agent_message",
    text:
      "Stalled 3 rounds. Leading explanation: local optimum on single models. Competing: ensembles untested. Cheapest falsifier: soft-vote(logistic, knn) — 2 evals.",
  }),
  at(68, {
    type: "hypothesis",
    id: "h11",
    text: "soft-vote(logistic, knn) beats the best single",
    kind: "recipe",
    family: "ensemble",
    control: "rf 0.958 (re-run, fresh seed)",
    treatment: "standardize + soft-vote(logistic, knn)",
    cost_est: 2,
    why: "Two kept, dissimilar learners; if errors are uncorrelated the vote wins. Falsifier: Δ < 0.003.",
  }),
  start(69, "e19", "h11", "confirm", spec("rf"), "daytona", SB[3]),
  start(69.2, "e20", "h11", "confirm", spec("", "standardize", {}, "soft_voting", ["logistic", "knn"]), "daytona", SB[4]),
  result(74, "e19", 0.958, 0.011, 0, "repeat", "control re-run on a fresh seed: 0.958 ± 0.011 — resolution holds", 3090),
  result(76, "e20", 0.963, 0.01, 0.005, "promote", "beats rf by 0.005; B survives, A falsified", 5870),
  at(77, {
    type: "progress",
    evals_done: 20,
    evals_grid: 180,
    best_metric: 0.963,
    best_single_metric: 0.958,
    history: [
      { round: 1, best: 0.958, mean: 0.9408, worst: 0.912 },
      { round: 2, best: 0.958, mean: 0.945, worst: 0.938 },
      { round: 3, best: 0.958, mean: 0.9433, worst: 0.94 },
      { round: 4, best: 0.958, mean: 0.9567, worst: 0.956 },
      { round: 5, best: 0.963, mean: 0.9605, worst: 0.958 },
    ],
  }),
  at(78, {
    type: "agent_message",
    text:
      "Ensembles are alive — A is falsified. Now the comfort recipe: stack everything. The information-gain gate lets it through once, because it's the cheapest test of superadditive harm.",
  }),
  at(79, {
    type: "hypothesis",
    id: "h12",
    text: "more members help monotonically",
    kind: "recipe",
    family: "ensemble",
    control: "soft-vote(logistic, knn) 0.963",
    treatment: "stack(all 8 → logistic)",
    cost_est: 1,
    why: "If widening always helps, the 8-member stack wins. If errors are correlated, the meta-learner overfits. Falsifier: treatment < 0.963.",
  }),
  start(80, "e21", "h12", "confirm", spec("logistic", "standardize", {}, "stacking", ["logistic", "knn", "tree", "rf", "gb", "nb", "svm", "mlp"]), "daytona", SB[5]),
  result(86, "e21", 0.949, 0.018, -0.014, "discard", "superadditive harm: 8 correlated members drown the meta-learner — below logistic alone (0.951)", 21400),
  at(87, {
    type: "agent_message",
    text:
      "Composite hurt: the stack of all 8 scores 0.949 — below logistic on its own. More members ≠ better; the meta-learner overfits correlated errors. Stacks stay ≤ 3 members.",
  }),
  at(88, {
    type: "progress",
    evals_done: 21,
    evals_grid: 180,
    best_metric: 0.963,
    best_single_metric: 0.958,
    history: [
      { round: 1, best: 0.958, mean: 0.9408, worst: 0.912 },
      { round: 2, best: 0.958, mean: 0.945, worst: 0.938 },
      { round: 3, best: 0.958, mean: 0.9433, worst: 0.94 },
      { round: 4, best: 0.958, mean: 0.9567, worst: 0.956 },
      { round: 5, best: 0.963, mean: 0.9605, worst: 0.958 },
      { round: 6, best: 0.963, mean: 0.956, worst: 0.949 },
    ],
  }),
  at(90, {
    type: "ask",
    id: "ask1",
    question: "Run the 6 remaining stacking recipes in Daytona? ~40s, 6 sandboxes.",
    options: [
      { id: "run", label: "Run", detail: "6 sandboxes · ~40 s · 6 evals" },
      { id: "skip", label: "Skip", detail: "Keep soft-vote(logistic, knn) 0.963 as champion" },
    ],
    reason: "Exceeds the per-round budget (3 evals); needs approval.",
  }),
  // ---- resumes after the ask is answered ----
  at(91, { type: "agent_message", text: "Running 6 stacking recipes, ≤ 3 members each — 6 Daytona sandboxes." }),
  at(92, {
    type: "hypothesis",
    id: "h13",
    text: "a small stack beats the vote",
    kind: "recipe",
    family: "ensemble",
    control: "soft-vote(logistic, knn) 0.963",
    treatment: "stacking family: 6 atomic recipes, ≤ 3 members",
    cost_est: 6,
    why: "A meta-learner over 2–3 diverse members can weight them better than a flat vote. Falsifier: none beats 0.966.",
  }),
  start(93, "e22", "h13", "confirm", STACK_LR, "daytona", SB[0]),
  start(93.2, "e23", "h13", "confirm", spec("logistic", "standardize", {}, "stacking", ["logistic", "knn"]), "daytona", SB[1]),
  start(93.4, "e24", "h13", "confirm", spec("logistic", "standardize", {}, "stacking", ["rf", "gb"]), "daytona", SB[2]),
  start(93.6, "e25", "h13", "confirm", spec("logistic", "standardize", {}, "stacking", ["logistic", "rf", "knn"]), "daytona", SB[3]),
  start(93.8, "e26", "h13", "confirm", spec("logistic", "standardize", {}, "stacking", ["svm", "rf"]), "daytona", SB[4]),
  start(94, "e27", "h13", "confirm", spec("logistic", "standardize", {}, "bagging", null), "daytona", SB[5]),
  result(99, "e22", 0.972, 0.009, 0.009, "promote", "beats soft-vote by 0.009; simplest 2-member stack", 9120),
  result(101, "e23", 0.961, 0.011, -0.002, "discard", "no gain over the vote of the same members", 8340),
  result(103, "e24", 0.96, 0.012, -0.003, "discard", "trees correlate; meta-learner has nothing to weigh", 11780),
  result(105, "e25", 0.968, 0.01, 0.005, "keep", "beats control, but loses to the 2-member stack — ties prefer simplicity", 10950),
  result(107, "e26", 0.966, 0.011, 0.003, "keep", "at the bar; svm adds a little margin", 9860),
  result(109, "e27", 0.95, 0.013, -0.013, "discard", "bagging a stable learner buys nothing", 7410),
  at(110, {
    type: "progress",
    evals_done: 27,
    evals_grid: 180,
    best_metric: 0.972,
    best_single_metric: 0.958,
    history: [
      { round: 1, best: 0.958, mean: 0.9408, worst: 0.912 },
      { round: 2, best: 0.958, mean: 0.945, worst: 0.938 },
      { round: 3, best: 0.958, mean: 0.9433, worst: 0.94 },
      { round: 4, best: 0.958, mean: 0.9567, worst: 0.956 },
      { round: 5, best: 0.963, mean: 0.9605, worst: 0.958 },
      { round: 6, best: 0.963, mean: 0.956, worst: 0.949 },
      { round: 7, best: 0.972, mean: 0.9628, worst: 0.95 },
    ],
  }),
  at(111, {
    type: "agent_message",
    text:
      "Ties prefer simplicity: the 2-member stack(logistic, rf) at 0.972 over the 3-member at 0.968. One repeat on a fresh seed so 0.972 isn't a resolution artifact.",
  }),
  at(112, {
    type: "hypothesis",
    id: "h14",
    text: "repeat: stack(logistic, rf → logistic) holds on a fresh seed",
    kind: "recipe",
    family: "ensemble",
    control: "rf 0.958",
    treatment: "standardize + stack(logistic, rf → logistic), seed 2",
    cost_est: 1,
    why: "Measurement-resolution check before promotion to champion. Falsifier: Δ < 0.003 vs rf.",
  }),
  start(113, "e28", "h14", "confirm", STACK_LR, "daytona", SB[0]),
  result(118, "e28", 0.971, 0.01, 0.013, "keep", "repeat holds: 0.971 vs rf 0.958", 9040),
  at(119, {
    type: "progress",
    evals_done: 28,
    evals_grid: 180,
    best_metric: 0.972,
    best_single_metric: 0.958,
    history: [
      { round: 1, best: 0.958, mean: 0.9408, worst: 0.912 },
      { round: 2, best: 0.958, mean: 0.945, worst: 0.938 },
      { round: 3, best: 0.958, mean: 0.9433, worst: 0.94 },
      { round: 4, best: 0.958, mean: 0.9567, worst: 0.956 },
      { round: 5, best: 0.963, mean: 0.9605, worst: 0.958 },
      { round: 6, best: 0.963, mean: 0.956, worst: 0.949 },
      { round: 7, best: 0.972, mean: 0.9628, worst: 0.95 },
      { round: 8, best: 0.972, mean: 0.971, worst: 0.971 },
    ],
  }),
  at(120, {
    type: "champion",
    spec: STACK_LR,
    metric: 0.972,
    delta_vs_best_single: 0.014,
    evals_used: 28,
    evals_grid_equivalent: 180,
  }),
  at(121, {
    type: "agent_message",
    text:
      "Champion → stack(logistic, rf → logistic) 0.972 · +0.014 over best single (rf 0.958) · 28 evals; grid would need 180. Sealed: polynomial. Harm found: stack(8 → logistic).",
  }),
  at(123, {
    type: "done",
    summary:
      "Composite champion stack(logistic, rf → logistic) 0.972 (+0.014 vs best single) in 28 evals of a 180-eval grid; 1 family sealed; 1 superadditive harm found.",
  }),
];

/* The foil: exhaustive grid, same catalog, still grinding. */
export const MOCK_GRID_EVENTS: TimedEvent[] = [
  at(0, {
    type: "lab_started",
    lab_id: "lab_grid",
    task: "breast_cancer",
    metric: "5-fold accuracy",
    catalog_summary: MOCK_CATALOG,
    providers: ["local"],
    budget: 180,
  }),
  at(0.2, {
    type: "agent_message",
    text: "Exhaustive grid over 180 recipes, in catalog order. No selection pressure, no families sealed, no falsifiers.",
  }),
  at(0.4, {
    type: "progress",
    evals_done: 71,
    evals_grid: 180,
    best_metric: 0.958,
    best_single_metric: 0.958,
    history: [
      { round: 1, best: 0.947, mean: 0.931, worst: 0.902 },
      { round: 2, best: 0.951, mean: 0.933, worst: 0.899 },
      { round: 3, best: 0.958, mean: 0.936, worst: 0.912 },
      { round: 4, best: 0.958, mean: 0.934, worst: 0.905 },
      { round: 5, best: 0.958, mean: 0.937, worst: 0.911 },
      { round: 6, best: 0.958, mean: 0.935, worst: 0.908 },
    ],
  }),
  start(0.6, "g72", "grid", "explore", spec("logistic", "polynomial(2)+pca(8)", { C: 0.1 }), "local"),
  start(0.7, "g73", "grid", "explore", spec("knn", "polynomial(2)+pca(8)", { k: 3 }), "local"),
  start(0.8, "g74", "grid", "explore", spec("tree", "polynomial(2)+pca(8)", { max_depth: 4 }), "local"),
  at(1, {
    type: "agent_message",
    text: "evaluating 71/180 · best so far rf 0.958 (single) · ~9 min remaining · 4 of the last 24 recipes were polynomial(2) variants that all lost.",
  }),
];

/* A finished lab from yesterday: replays instantly. */
const Y = BASE_TS - 86_400 + 3 * 3600;
const yat = (t: number, ev: AirlockEvent): TimedEvent => ({ t: 0, event: { ...ev, ts: Y + t } });
export const MOCK_WINE_EVENTS: TimedEvent[] = [
  yat(0, {
    type: "lab_started",
    lab_id: "lab_wine",
    task: "wine",
    metric: "5-fold accuracy",
    catalog_summary: MOCK_CATALOG,
    providers: ["local", "daytona"],
    budget: 40,
  }),
  yat(1, { type: "agent_message", text: "Starting on `wine` · metric: 5-fold accuracy · catalog: 31 factors in 4 families." }),
  yat(600, { type: "experiment_started", id: "w1", hypothesis_id: "w", lane: "confirm", spec: spec("logistic", "standardize"), runner: "daytona", sandbox_id: SB[0] }),
  yat(601, { type: "experiment_started", id: "w2", hypothesis_id: "w", lane: "confirm", spec: spec("knn", "quantile"), runner: "daytona", sandbox_id: SB[1] },),
  yat(620, { type: "experiment_result", id: "w1", metric: 0.983, std: 0.014, delta_vs_control: 0.028, cost_ms: 4100, gate: "promote", reason: "scaling is everything on wine" }),
  yat(622, { type: "experiment_result", id: "w2", metric: 0.949, std: 0.02, delta_vs_control: -0.011, cost_ms: 3900, gate: "discard", reason: "quantile flattens the separating tails" }),
  yat(1300, { type: "family_sealed", family: "quantile", evidence: [{ spec: spec("knn", "quantile"), delta: -0.011 }], text: "Sealed family `quantile` — isolated, pair, triple all below the bar." }),
  yat(1900, {
    type: "champion",
    spec: spec("logistic", "standardize", {}, "soft_voting", ["logistic", "rf"]),
    metric: 0.989,
    delta_vs_best_single: 0.006,
    evals_used: 17,
    evals_grid_equivalent: 180,
  }),
  yat(1901, { type: "agent_message", text: "Champion → standardize + soft-vote(logistic, rf) 0.989 · +0.006 over best single · 17 evals (grid: 180)." }),
  yat(1902, {
    type: "progress",
    evals_done: 17,
    evals_grid: 180,
    best_metric: 0.989,
    best_single_metric: 0.983,
    history: [
      { round: 1, best: 0.955, mean: 0.92, worst: 0.87 },
      { round: 2, best: 0.983, mean: 0.951, worst: 0.93 },
      { round: 3, best: 0.983, mean: 0.96, worst: 0.949 },
      { round: 4, best: 0.989, mean: 0.978, worst: 0.966 },
    ],
  }),
  yat(1903, { type: "done", summary: "Composite champion soft-vote(logistic, rf) 0.989 in 17 evals." }),
];
