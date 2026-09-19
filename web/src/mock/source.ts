/* Mock transport: replays the canned timelines with real timers, pauses on
   `ask` until answered (or 45 s), and answers steering messages. Mirrors the
   API surface in src/api.ts so the rest of the app never knows. */
import type { AirlockEvent, LabRow } from "../types.ts";
import { MOCK_BC_EVENTS, MOCK_DURATION_SCALE, MOCK_GRID_EVENTS, MOCK_WINE_EVENTS, type TimedEvent } from "./events.ts";

const ASK_AUTO_RESUME_MS = 45_000;

interface MockLab {
  row: LabRow;
  timeline: TimedEvent[];
}

const nowIso = () => new Date().toISOString();
const yesterdayIso = () => new Date(Date.now() - 86_400_000 + 3 * 3_600_000).toISOString();

const labs = new Map<string, MockLab>([
  [
    "lab_bc",
    {
      row: {
        id: "lab_bc",
        title: "breast_cancer · composite",
        last_message: "Starting on `breast_cancer` · 5-fold accuracy · 31 factors",
        updated: nowIso(),
        status: "running",
        color: "#3B82F6",
      },
      timeline: MOCK_BC_EVENTS,
    },
  ],
  [
    "lab_grid",
    {
      row: {
        id: "lab_grid",
        title: "Grid Search",
        last_message: "evaluating 71/180 · best 0.958 (single) · ~9 min left",
        updated: new Date(Date.now() - 3 * 60_000).toISOString(),
        status: "running",
        color: "#F97316",
      },
      timeline: MOCK_GRID_EVENTS,
    },
  ],
  [
    "lab_wine",
    {
      row: {
        id: "lab_wine",
        title: "wine · composite",
        last_message: "★ Champion → soft-vote(logistic, rf) 0.989 · 17 evals",
        updated: yesterdayIso(),
        status: "done",
        color: "#8B5CF6",
      },
      timeline: MOCK_WINE_EVENTS,
    },
  ],
]);

function speed(): number {
  try {
    const s = Number(new URLSearchParams(window.location.search).get("speed"));
    return s > 0 ? s : 1;
  } catch {
    return 1;
  }
}

interface Session {
  labId: string;
  timeline: TimedEvent[];
  cursor: number;
  timers: number[];
  onEvent: (e: AirlockEvent) => void;
  paused: { askId: string; autoTimer: number } | null;
  closed: boolean;
  openedAt: number;
}

const sessions = new Map<string, Session>();
let created = 0;

function touch(labId: string, text: string, status?: string) {
  const lab = labs.get(labId);
  if (!lab) return;
  lab.row = { ...lab.row, last_message: text, updated: nowIso(), status: status ?? lab.row.status };
}

function schedule(s: Session) {
  if (s.closed) return;
  const sp = speed();
  const base = s.timeline[s.cursor];
  if (!base) return;
  const t0 = base.t;
  // Emit everything from the cursor on, timed relative to the current head,
  // stopping after an ask so the lab waits for an answer.
  for (let i = s.cursor; i < s.timeline.length; i++) {
    const item = s.timeline[i]!;
    const delay = Math.max(0, ((item.t - t0) * MOCK_DURATION_SCALE * 1000) / sp);
    const timer = window.setTimeout(() => {
      if (s.closed) return;
      s.cursor = i + 1;
      emit(s, item.event);
      if (item.event.type === "ask") {
        const askId = item.event.id;
        const autoTimer = window.setTimeout(() => MockLabSource.answer(s.labId, askId, "run"), ASK_AUTO_RESUME_MS);
        s.paused = { askId, autoTimer };
      }
    }, delay);
    s.timers.push(timer);
    if (item.event.type === "ask") break;
  }
}

function emit(s: Session, e: AirlockEvent) {
  s.onEvent(e);
  if (e.type === "agent_message") touch(s.labId, e.text);
  else if (e.type === "champion") touch(s.labId, `★ Champion → ${e.metric.toFixed(3)} · ${e.evals_used} evals`);
  else if (e.type === "ask") touch(s.labId, e.question, "waiting");
  else if (e.type === "done") touch(s.labId, e.summary, "done");
  else if (e.type === "family_sealed") touch(s.labId, e.text);
}

function clearTimers(s: Session) {
  for (const t of s.timers) window.clearTimeout(t);
  s.timers = [];
  if (s.paused) window.clearTimeout(s.paused.autoTimer);
}

export const MockLabSource = {
  open(labId: string, onEvent: (e: AirlockEvent) => void, onStatus?: (s: "open" | "error") => void) {
    const lab = labs.get(labId);
    if (!lab) {
      onStatus?.("error");
      return { close() {} };
    }
    const existing = sessions.get(labId);
    if (existing) {
      clearTimers(existing);
      existing.closed = true;
    }
    const s: Session = {
      labId,
      timeline: lab.timeline,
      cursor: 0,
      timers: [],
      onEvent,
      paused: null,
      closed: false,
      openedAt: Date.now(),
    };
    // Replay from 0 on connect: everything already "emitted" in a previous
    // session comes back instantly, then the rest streams live.
    const replayTo = existing ? existing.cursor : 0;
    for (let i = 0; i < replayTo; i++) onEvent(lab.timeline[i]!.event);
    s.cursor = replayTo;
    sessions.set(labId, s);
    onStatus?.("open");
    if (existing?.paused) {
      s.paused = { askId: existing.paused.askId, autoTimer: window.setTimeout(() => MockLabSource.answer(labId, existing.paused!.askId, "run"), ASK_AUTO_RESUME_MS) };
    } else {
      schedule(s);
    }
    return {
      close() {
        // Keep the session's cursor so a re-open replays; just stop delivering.
        s.onEvent = () => {};
      },
    };
  },

  answer(labId: string, askId: string, optionId: string) {
    const s = sessions.get(labId);
    if (!s || !s.paused || s.paused.askId !== askId) return;
    window.clearTimeout(s.paused.autoTimer);
    s.paused = null;
    if (optionId === "skip") {
      // Skip the stacking round: jump straight to a champion built from the vote.
      const t = s.timeline[s.cursor]?.t ?? 0;
      const ts = s.timeline[s.cursor]?.event.ts ?? Math.floor(Date.now() / 1000);
      const tail: TimedEvent[] = [
        { t, event: { type: "agent_message", ts, text: "Skipping the stacking round. Promoting the vote as champion.", } },
        {
          t: t + 1,
          event: {
            type: "champion",
            ts: ts + 20,
            spec: { transform: "standardize", model: "", ensemble: "soft_voting", hyper: {}, members: ["logistic", "knn"] },
            metric: 0.963,
            delta_vs_best_single: 0.005,
            evals_used: 21,
            evals_grid_equivalent: 180,
          },
        },
        { t: t + 2, event: { type: "done", ts: ts + 40, summary: "Champion soft-vote(logistic, knn) 0.963 in 21 evals (stacking round skipped)." } },
      ];
      s.timeline = [...s.timeline.slice(0, s.cursor), ...tail];
    }
    schedule(s);
  },

  steer(labId: string, text: string) {
    const s = sessions.get(labId);
    if (!s) return;
    const ts = Math.floor(Date.now() / 1000);
    const lower = text.toLowerCase();
    let reply: string;
    const focus = /focus (?:on )?(?:family )?`?(\w+)`?/.exec(lower);
    if (/^stop\b/.test(lower)) {
      clearTimers(s);
      s.closed = true;
      window.setTimeout(() => s.onEvent({ type: "done", ts, summary: "Stopped by user. Best so far kept as champion." }), 600);
      touch(labId, "Stopped by user.", "done");
      return;
    } else if (focus) {
      reply = `Focusing on family \`${focus[1]}\`: the next selection step only draws hypotheses from it, subject to the information-gain gate.`;
    } else if (/why/.test(lower)) {
      reply = "Because it failed its declared falsifier: the treatment landed below control + min_delta on the same folds. The ledger has the spec, control, treatment and delta.";
    } else if (/run (\d+) more/.test(lower)) {
      reply = `Budget extended by ${/run (\d+) more/.exec(lower)?.[1]} evals. Spending them on the cheapest unresolved falsifiers.`;
    } else {
      reply = `Noted — “${text}”. Folding it into the next selection step.`;
    }
    window.setTimeout(() => {
      if (!s.closed) s.onEvent({ type: "agent_message", ts: ts + 1, text: reply });
      touch(labId, reply);
    }, 900);
  },

  create(prompt: string): string {
    created += 1;
    const id = `lab_new_${created}`;
    const title = prompt.trim().slice(0, 40) || `Lab ${created}`;
    const timeline = MOCK_BC_EVENTS.map((x) =>
      x.event.type === "lab_started" ? { ...x, event: { ...x.event, lab_id: id } } : x,
    );
    labs.set(id, {
      row: { id, title, last_message: "Starting…", updated: nowIso(), status: "running" },
      timeline,
    });
    return id;
  },
};

export function mockLabs(): LabRow[] {
  return [...labs.values()]
    .map((l) => l.row)
    .sort((a, b) => new Date(b.updated).getTime() - new Date(a.updated).getTime());
}
