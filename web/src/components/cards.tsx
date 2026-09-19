/* Chat surface pieces: bubbles, the ✓/✗/⊘/★ checklist card, hypothesis,
   stagnation, champion and ask cards. Classes mirror rakazo's Shell/AskCard. */
import { useState } from "react";
import { fmtClock, fmtDelta, fmtMetric, recipeName } from "../format.ts";
import { gateLabel, gateMark, isHarm, type ResultRow } from "../state.ts";
import type { AskEvent, CardMark, ChampionEvent, FamilySealedEvent, HypothesisEvent, MessageCard, StagnationEvent } from "../types.ts";

/* Inline code spans: `like this` → <code>. Keeps narration copy-pasteable. */
export function Rich({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") ? (
          <code key={i} className="rounded-[5px] bg-background/70 px-1 py-px font-mono text-[13px] text-foreground">
            {p.slice(1, -1)}
          </code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

export function TimeDivider({ ts }: { ts: number }) {
  return <div className="py-1 text-center text-[12px] text-muted-foreground/60">{fmtClock(ts)}</div>;
}

export function AgentBubble({ text, cards }: { text: string; cards?: MessageCard[] }) {
  return (
    <div className="flex w-fit max-w-full flex-col gap-2">
      <div className="flex w-fit max-w-full justify-start">
        <div
          data-testid="message-bot-bubble"
          className="max-w-full rounded-[20px] bg-muted px-[18px] py-3 text-[15.5px] leading-[1.5] text-foreground/90 md:max-w-[74%]"
          dir="auto"
        >
          <Rich text={text} />
        </div>
      </div>
      {cards && cards.length > 0 ? (
        <Card>
          {cards.map((c, i) => (
            <CardLine key={i} mark={c.mark} label={c.label} text={c.text} metric={c.metric} />
          ))}
        </Card>
      ) : null}
    </div>
  );
}

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex w-full justify-end">
      <div
        data-testid="message-user-bubble"
        className="max-w-[74%] whitespace-pre-wrap rounded-[20px] bg-chat-user px-[18px] py-3 text-[15.5px] leading-[1.45] text-chat-user-foreground [overflow-wrap:anywhere]"
        dir="auto"
      >
        {text}
      </div>
    </div>
  );
}

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className="flex justify-start">
      <div className={`flex w-full max-w-[560px] flex-col gap-2 rounded-[20px] border border-border bg-card px-5 py-4 ${className}`}>
        {children}
      </div>
    </div>
  );
}

function markGlyph(mark: CardMark): { glyph: string; tone: string } {
  switch (mark) {
    case "ok":
    case "✓":
      return { glyph: "✓", tone: "text-success" };
    case "no":
    case "✗":
      return { glyph: "✗", tone: "text-destructive" };
    case "seal":
    case "⊘":
      return { glyph: "⊘", tone: "text-muted-foreground" };
    case "star":
    case "★":
      return { glyph: "★", tone: "text-warning" };
    case "○":
      return { glyph: "○", tone: "text-muted-foreground/70" };
    default:
      return { glyph: "·", tone: "text-muted-foreground" };
  }
}

export function CardLine({
  mark,
  label,
  text,
  metric,
  note,
}: {
  mark: CardMark | "↻" | "!";
  label: string;
  text: string;
  metric?: string;
  note?: string;
}) {
  const m =
    mark === "↻"
      ? { glyph: "↻", tone: "text-muted-foreground" }
      : mark === "!"
        ? { glyph: "!", tone: "text-warning" }
        : markGlyph(mark);
  return (
    <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[15px]">
      <span className={`w-3 shrink-0 ${m.tone}`}>{m.glyph}</span>
      <span className="font-semibold text-foreground">{label}</span>
      <span className="text-muted-foreground">→</span>
      <span className="text-foreground/90">{text}</span>
      {metric ? <span className="tabular-nums text-muted-foreground">{metric}</span> : null}
      {note ? <span className="text-[12.5px] text-warning">· {note}</span> : null}
    </div>
  );
}

export function ResultsCard({ rows }: { rows: ResultRow[] }) {
  return (
    <Card>
      {rows.map((r) => (
        <CardLine
          key={r.id}
          mark={gateMark(r.gate)}
          label={gateLabel(r.gate)}
          text={r.name}
          metric={`${fmtMetric(r.metric)}${r.delta !== null && r.delta !== undefined ? ` (${fmtDelta(r.delta)}${r.gate === "explore_only" ? "" : " vs control"})` : ""}`}
          note={isHarm(r) ? "superadditive harm" : r.gate === "crash" ? "crash — not a scientific discard" : undefined}
        />
      ))}
    </Card>
  );
}

export function SealCard({ ev }: { ev: FamilySealedEvent }) {
  return (
    <Card>
      <CardLine mark="seal" label="Sealed" text={ev.text.replace(/^Sealed family\s*/i, "")} />
      <div className="mt-1 flex flex-wrap gap-1.5 ps-[22px]">
        {ev.evidence.map((e, i) => (
          <span key={i} className="rounded-md border border-border bg-muted px-2 py-0.5 text-[11.5px] tabular-nums text-muted-foreground line-through decoration-muted-foreground/60">
            {recipeName(e.spec)} {fmtDelta(e.delta)}
          </span>
        ))}
      </div>
    </Card>
  );
}

export function ChampionCard({ ev }: { ev: ChampionEvent }) {
  return (
    <Card className="border-warning/40">
      <CardLine
        mark="star"
        label="Champion"
        text={recipeName(ev.spec)}
        metric={`${fmtMetric(ev.metric)} · ${fmtDelta(ev.delta_vs_best_single)} over best single`}
      />
      <div className="ps-[22px] text-[13px] text-muted-foreground">
        {ev.evals_used} evals <span className="text-muted-foreground/60">(grid would need {ev.evals_grid_equivalent})</span>
      </div>
    </Card>
  );
}

export function HypothesisBubble({ hyp }: { hyp: HypothesisEvent }) {
  return (
    <div className="flex w-fit max-w-full justify-start">
      <div className="max-w-full rounded-[20px] bg-muted px-[18px] py-3 text-[15.5px] leading-[1.5] text-foreground/90 md:max-w-[74%]">
        <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70">
          <span>{hyp.kind === "recipe" ? "atomic recipe" : "factor"}</span>
          <span className="text-muted-foreground/40">·</span>
          <span>{hyp.family}</span>
          <span className="text-muted-foreground/40">·</span>
          <span>~{hyp.cost_est} eval{hyp.cost_est === 1 ? "" : "s"}</span>
        </div>
        <div>
          <Rich text={hyp.text} />
        </div>
        <div className="mt-1.5 text-[13px] text-muted-foreground">
          control <span className="text-foreground/80">{hyp.control}</span> vs treatment{" "}
          <span className="text-foreground/80">{hyp.treatment}</span>
        </div>
        {hyp.why ? (
          <div className="mt-1 text-[13px] text-muted-foreground/80">
            <Rich text={hyp.why} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function LaunchLine({ count, runner, lane }: { count: number; runner: string; lane: string }) {
  return (
    <div className="flex items-center gap-2 ps-1 text-[12.5px] text-muted-foreground/70">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-success/80" style={{ animation: "rkPulse 1.6s ease-in-out infinite" }} />
      <span>
        {lane} · {count} experiment{count === 1 ? "" : "s"} → {runner === "daytona" ? `${count} Daytona sandbox${count === 1 ? "" : "es"}` : "local pool"}
      </span>
    </div>
  );
}

export function StagnationCard({ ev }: { ev: StagnationEvent }) {
  return (
    <Card>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-warning">
        Stalled · {ev.grade.replace(/_/g, " ")}
      </div>
      <ol className="mt-0.5 space-y-1.5 text-[14.5px] leading-[1.45] text-foreground/90">
        {ev.explanations.map((x, i) => (
          <li key={i} className="flex gap-2.5">
            <span className="grid h-[22px] w-[22px] shrink-0 place-items-center rounded-[7px] bg-muted text-[12px] font-medium text-foreground/75">
              {String.fromCharCode(65 + i)}
            </span>
            <span className={i === 0 ? "" : "text-foreground/75"}>{x}</span>
          </li>
        ))}
      </ol>
      <div className="mt-1 text-[13px] text-muted-foreground">
        <span className="text-foreground/80">Separates them:</span> {ev.separating_observation}
      </div>
      <div className="text-[13px] text-muted-foreground">
        <span className="text-foreground/80">Next:</span> {ev.next}
      </div>
    </Card>
  );
}

export function AskCard({
  ev,
  answered,
  canAnswer,
  onAnswer,
}: {
  ev: AskEvent;
  answered: string | null;
  canAnswer: boolean;
  onAnswer: (optionId: string) => Promise<void>;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function choose(id: string) {
    if (pending) return;
    setPending(id);
    setError(null);
    try {
      await onAnswer(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not submit this answer");
    } finally {
      setPending(null);
    }
  }
  const answeredLabel = answered ? (ev.options.find((o) => o.id === answered)?.label ?? answered) : null;
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[560px] rounded-2xl border border-border bg-card px-5 py-4 md:max-w-[74%]">
        <div className="text-[15.5px] leading-[1.5] text-foreground">
          <Rich text={ev.question} />
        </div>
        {ev.reason ? <div className="mt-1 text-[13px] text-muted-foreground">{ev.reason}</div> : null}
        {answeredLabel ? (
          <div className="mt-3.5 text-[13.5px] font-medium text-success">Answered: {answeredLabel}</div>
        ) : !canAnswer ? (
          <div className="mt-3.5 text-[13.5px] font-medium text-muted-foreground">No longer active</div>
        ) : (
          <div className="mt-3.5 space-y-1.5">
            {ev.options.map((o, i) => (
              <button
                key={o.id}
                type="button"
                disabled={pending !== null}
                onClick={() => void choose(o.id)}
                className={`flex h-auto w-full items-start justify-start gap-3 whitespace-normal rounded-lg border px-3.5 py-3 text-start text-sm font-normal transition-all disabled:pointer-events-none disabled:opacity-50 ${
                  i === 0
                    ? "border-transparent bg-primary text-primary-foreground hover:bg-primary/80"
                    : "border-border bg-input/30 text-foreground hover:bg-input/50"
                }`}
              >
                <span className="flex-1">
                  <span className="block text-[14.5px]">{pending === o.id ? "Sending…" : o.label}</span>
                  {o.detail ? <span className={`block text-[12.5px] ${i === 0 ? "text-primary-foreground/70" : "text-muted-foreground"}`}>{o.detail}</span> : null}
                </span>
              </button>
            ))}
          </div>
        )}
        {error ? <p className="mt-3 text-[13px] text-destructive">{error}</p> : null}
      </div>
    </div>
  );
}

export function DoneLine({ summary }: { summary: string }) {
  return (
    <div className="flex items-center gap-2 ps-1 text-[12.5px] text-muted-foreground/70">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground/60" />
      <span>Done · {summary}</span>
    </div>
  );
}
