/// <reference types="node" />
/* Run: cd web && node --test src/state.test.ts   (Node ≥ 22.6 strips types natively) */
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseEvent } from "./api.ts";
import { fmtDelta, recipeName } from "./format.ts";
import { MOCK_BC_EVENTS } from "./mock/events.ts";
import { initialState, reduce, runningExperiments, type LabState } from "./state.ts";
import type { AirlockEvent } from "./types.ts";

function replay(events: AirlockEvent[], stopAt = Infinity): LabState {
  let s = initialState();
  let t = 1_700_000_000_000;
  for (const [i, e] of events.entries()) {
    if (i >= stopAt) break;
    t += 1000;
    s = reduce(s, { type: "event", event: e, receivedAt: t });
  }
  return s;
}

test("recipeName renders singles, transforms, stacks and votes", () => {
  assert.equal(recipeName({ transform: "none", model: "logistic", ensemble: null, hyper: {}, members: null }), "logistic");
  assert.equal(
    recipeName({ transform: "standardize", model: "logistic", ensemble: null, hyper: {}, members: null }),
    "standardize + logistic",
  );
  assert.equal(
    recipeName({ transform: "standardize", model: "logistic", ensemble: "stacking", hyper: {}, members: ["logistic", "rf"] }),
    "standardize + stack(logistic, rf → logistic)",
  );
  assert.equal(
    recipeName({ transform: "none", model: "", ensemble: "soft_voting", hyper: {}, members: ["logistic", "knn"] }),
    "soft-vote(logistic, knn)",
  );
  assert.equal(recipeName({ transform: "none", model: "rf", ensemble: null, hyper: { max_depth: 8 }, members: null }), "rf(max_depth=8)");
});

test("fmtDelta uses typographic minus and explicit plus", () => {
  assert.equal(fmtDelta(0.004), "+0.004");
  assert.equal(fmtDelta(-0.009), "−0.009");
  assert.equal(fmtDelta(null), "");
});

test("parseEvent accepts type/event/kind keys and rejects unknown", () => {
  assert.equal(parseEvent('{"type":"done","summary":"x"}')?.type, "done");
  assert.equal(parseEvent('{"event":"progress","evals_done":1}')?.type, "progress");
  assert.equal(parseEvent('{"summary":"x"}', "done")?.type, "done");
  assert.equal(parseEvent('{"type":"tool_call"}'), null);
  assert.equal(parseEvent("not json"), null);
});

test("full mock replay reaches a composite champion, seals a family, surfaces harm", () => {
  const events = MOCK_BC_EVENTS.map((m) => m.event);
  const s = replay(events);
  assert.equal(s.status, "done");
  assert.ok(s.champion, "champion emitted");
  assert.equal(s.champion?.spec.ensemble, "stacking");
  assert.ok(s.champion!.delta_vs_best_single >= 0.003);
  assert.ok(s.champion!.evals_used < s.champion!.evals_grid_equivalent);
  assert.deepEqual(s.sealed, ["polynomial"]);
  assert.ok(s.harms.length >= 1, "superadditive harm row surfaced");
  assert.equal(s.running.length, 0, "nothing left running at done");
  // Every experiment_result matched an experiment_started.
  for (const exp of Object.values(s.experiments)) assert.ok(exp.result, `result for ${exp.id}`);
  // evals_used in the champion event equals the results counted by the reducer.
  assert.equal(s.champion!.evals_used, s.evalsDone);
  // The progress history is monotone non-decreasing in best.
  const hist = s.progress!.history;
  for (let i = 1; i < hist.length; i++) assert.ok(hist[i]!.best >= hist[i - 1]!.best);
  assert.equal(s.progress!.evals_done, s.evalsDone);
});

test("consecutive results fold into one card; launches fold by runner", () => {
  const events = MOCK_BC_EVENTS.map((m) => m.event);
  const firstResultIdx = events.findIndex((e) => e.type === "experiment_result");
  const s = replay(events, firstResultIdx + 8); // the 8-model explore screen
  const results = s.items.filter((i) => i.kind === "results");
  assert.equal(results.length, 1);
  assert.equal(results[0]!.kind === "results" ? results[0]!.rows.length : 0, 8);
  const launches = s.items.filter((i) => i.kind === "launch");
  assert.equal(launches.length, 1);
});

test("ask pauses the lab; answering or a later event resumes it", () => {
  const events = MOCK_BC_EVENTS.map((m) => m.event);
  const askIdx = events.findIndex((e) => e.type === "ask");
  const paused = replay(events, askIdx + 1);
  assert.equal(paused.status, "waiting");
  assert.ok(paused.openAskId);
  const answered = reduce(paused, { type: "answered", askId: paused.openAskId!, optionId: "run" });
  assert.equal(answered.status, "running");
  assert.equal(answered.asks[paused.openAskId!]!.answered, "run");
  const movedOn = replay(events, askIdx + 2);
  assert.equal(movedOn.openAskId, null);
});

test("running list tracks daytona sandboxes in flight", () => {
  const events = MOCK_BC_EVENTS.map((m) => m.event);
  const idx = events.findIndex((e) => e.type === "experiment_started" && e.runner === "daytona");
  const s = replay(events, idx + 4);
  const running = runningExperiments(s);
  assert.ok(running.length >= 1);
  assert.ok(running.every((r) => r.runner === "daytona" && r.sandboxId));
});

test("timestamp dividers appear on ≥10 minute gaps", () => {
  let s = initialState();
  s = reduce(s, { type: "event", event: { type: "agent_message", text: "a", ts: 1000 }, receivedAt: 0 });
  s = reduce(s, { type: "event", event: { type: "agent_message", text: "b", ts: 1100 }, receivedAt: 0 });
  s = reduce(s, { type: "event", event: { type: "agent_message", text: "c", ts: 1700 }, receivedAt: 0 });
  assert.equal(s.items.filter((i) => i.kind === "divider").length, 2);
});
