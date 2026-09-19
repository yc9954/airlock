/* Right pane — "Airlock's computer": screen card with the fitness chart,
   running sandboxes, catalog & families (sealed = line-through), champion,
   and the Research routines list. Raw experiment lifecycle lives here. */
import { useEffect, useState } from "react";
import { fmtDelta, fmtMetric, fmtMs, recipeName } from "../format.ts";
import { factorCount, llmProvider, runningExperiments, type LabState } from "../state.ts";
import { FitnessChart } from "./FitnessChart.tsx";
import { ClockDotIcon, SettingsIcon, XIcon } from "./icons.tsx";

const ROUTINES: { name: string; when: string }[] = [
  { name: "Explore screen", when: "each round" },
  { name: "Confirm one axis", when: "control vs treatment" },
  { name: "Family seal", when: "iso · pair · triple" },
  { name: "Why-critique", when: "3 stalled rounds" },
  { name: "Cheapest falsifier", when: "every selection" },
];

function useTick(active: boolean, ms = 250): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [active, ms]);
  return now;
}

function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-2 mt-5 flex items-center justify-between">
      <span className="text-[14px] text-foreground">{children}</span>
      {right ? <span className="text-[12.5px] text-muted-foreground">{right}</span> : null}
    </div>
  );
}

export function Panel({ state, open, onClose }: { state: LabState; open: boolean; onClose: () => void }) {
  const running = runningExperiments(state);
  const now = useTick(running.length > 0);
  const progress = state.progress;
  const daytona = running.filter((r) => r.runner === "daytona").length;
  const llm = llmProvider(state);
  const recent = state.recent.map((id) => state.experiments[id]).filter((e) => e && e.result);
  const catalog = state.catalog;
  const families = catalog ? Object.entries(catalog.families) : [];
  const sealedSet = new Set(state.sealed.map((s) => s.toLowerCase()));
  const isSealed = (family: string, factor: string) => {
    const f = factor.toLowerCase();
    return sealedSet.has(family.toLowerCase()) || [...sealedSet].some((s) => f.startsWith(s));
  };

  return (
    <aside
      data-testid="side-panel"
      data-panel={open ? "computer" : "closed"}
      className={`absolute inset-y-0 end-0 z-20 flex min-h-0 shrink-0 flex-col overflow-hidden bg-background transition-[width] duration-150 ease-out md:relative ${
        open ? "w-full max-w-[384px] border-s border-sidebar-border md:w-[384px] md:max-w-none" : "pointer-events-none w-0"
      }`}
    >
      {open ? (
        <div className="rk-scroll h-full w-full overflow-y-auto px-5 py-[17px] md:w-[384px]">
          <div className="mb-4 flex items-center justify-between">
            <span className="text-[13.5px] text-muted-foreground">Airlock's computer</span>
            <div className="flex items-center gap-1">
              <button type="button" aria-label="Settings" className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground">
                <SettingsIcon size={16} />
              </button>
              <button type="button" aria-label="Close panel" onClick={onClose} className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground">
                <XIcon size={16} />
              </button>
            </div>
          </div>

          {/* Screen card, like rakazo's live-desktop preview */}
          <div className="rounded-2xl border border-border bg-card p-3">
            <div className="mb-2 flex items-center gap-2 rounded-lg bg-muted px-2.5 py-1.5">
              <span className="flex gap-1" aria-hidden="true">
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
              </span>
              <span className="truncate font-mono text-[10.5px] text-muted-foreground">
                {state.providers.includes("daytona") ? "daytona" : "local"} · {state.task || "—"} · {state.metric || "metric"}
              </span>
              {llm ? (
                <span className="ms-auto shrink-0 rounded-md border border-border bg-background px-1.5 py-[1px] font-mono text-[10px] text-muted-foreground" title="Open model that narrates and answers `why` — never scores">
                  {llm}
                </span>
              ) : null}
            </div>
            <div className="flex items-baseline justify-between px-1">
              <span className="text-[13px] font-medium text-foreground">
                {progress ? `Best ${fmtMetric(progress.best_metric)}` : "Fitness per round"}
              </span>
              <span className="text-[11.5px] tabular-nums text-muted-foreground">
                {progress ? `${progress.evals_done} / ${progress.evals_grid} grid evals` : `${state.evalsDone} evals`}
              </span>
            </div>
            <div className="mt-1">
              <FitnessChart history={progress?.history ?? []} bestSingle={progress?.best_single_metric ?? null} metric={state.metric || "metric"} />
            </div>
            {progress ? (
              <div className="mt-2 h-[4px] w-full overflow-hidden rounded-full bg-accent" aria-label="evals used vs grid">
                <div className="h-full rounded-full bg-link/70" style={{ width: `${Math.min(100, (progress.evals_done / Math.max(1, progress.evals_grid)) * 100)}%` }} />
              </div>
            ) : null}
          </div>

          <SectionTitle right={running.length ? `${running.length} live${daytona ? ` · ${daytona} Daytona` : ""}` : "idle"}>
            Running sandboxes
          </SectionTitle>
          <div className="space-y-0.5">
            {running.length === 0 && recent.length === 0 ? (
              <div className="text-[12.5px] text-muted-foreground/70">Nothing in flight.</div>
            ) : null}
            {running.map((r) => (
              <div key={r.id} className="flex items-center gap-2.5 rounded-lg px-1 py-1 text-[13px]">
                <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-success" style={{ animation: "rkPulse 1.2s ease-in-out infinite" }} />
                <span className="w-[72px] shrink-0 truncate font-mono text-[11.5px] text-muted-foreground">{r.sandboxId ?? `local·${r.id}`}</span>
                <span className="min-w-0 flex-1 truncate text-foreground/90">{recipeName(r.spec)}</span>
                <span className="shrink-0 tabular-nums text-[11.5px] text-muted-foreground">{fmtMs(now - r.startedAt)}</span>
              </div>
            ))}
            {recent.slice(0, Math.max(0, 5 - running.length)).map((r) => (
              <div key={r!.id} className="flex items-center gap-2.5 rounded-lg px-1 py-1 text-[13px] opacity-70">
                <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-muted-foreground/40" />
                <span className="w-[72px] shrink-0 truncate font-mono text-[11.5px] text-muted-foreground">{r!.sandboxId ?? `local·${r!.id}`}</span>
                <span className="min-w-0 flex-1 truncate text-foreground/80">{recipeName(r!.spec)}</span>
                <span className="shrink-0 tabular-nums text-[11.5px] text-muted-foreground">{fmtMs(r!.result!.cost_ms)}</span>
              </div>
            ))}
          </div>

          <SectionTitle right={catalog ? `${factorCount(catalog)} factors · ${families.length} families` : undefined}>
            Catalog & families
          </SectionTitle>
          {families.length === 0 ? (
            <div className="text-[12.5px] text-muted-foreground/70">Catalog arrives with lab_started.</div>
          ) : (
            <div className="space-y-2.5">
              {families.map(([family, factors]) => {
                const sealedFamily = sealedSet.has(family.toLowerCase());
                return (
                  <div key={family}>
                    <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                      <span className={sealedFamily ? "line-through" : ""}>{family}</span>
                      {sealedFamily ? <span className="text-[10px] normal-case tracking-normal text-muted-foreground">⊘ sealed</span> : null}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {(Array.isArray(factors) ? factors : []).map((f) => {
                        const sealed = isSealed(family, f);
                        return (
                          <span
                            key={f}
                            className={`rounded-md border border-border bg-muted px-2 py-0.5 text-[11px] ${
                              sealed ? "text-muted-foreground/50 line-through decoration-muted-foreground/60" : "text-muted-foreground"
                            }`}
                          >
                            {f}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <SectionTitle>Champion</SectionTitle>
          {state.champion ? (
            <div className="rounded-2xl border border-warning/40 bg-card px-4 py-3">
              <div className="flex items-baseline gap-2 text-[14.5px]">
                <span className="text-warning">★</span>
                <span className="min-w-0 flex-1 truncate font-medium text-foreground">{recipeName(state.champion.spec)}</span>
              </div>
              <div className="mt-1 text-[13px] tabular-nums text-muted-foreground">
                {fmtMetric(state.champion.metric)} · {fmtDelta(state.champion.delta_vs_best_single)} vs best single
              </div>
              <div className="text-[12.5px] tabular-nums text-muted-foreground/70">
                {state.champion.evals_used} evals · grid {state.champion.evals_grid_equivalent}
              </div>
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-border px-4 py-3 text-[12.5px] text-muted-foreground/70">
              {progress
                ? `No promotion yet · best single ${fmtMetric(progress.best_single_metric)}`
                : "Only a `promote` replaces the champion."}
              {state.harms.length ? (
                <div className="mt-1 text-warning/90">
                  {state.harms.length} composite{state.harms.length === 1 ? "" : "s"} shown to hurt
                </div>
              ) : null}
            </div>
          )}

          <SectionTitle>Research routines</SectionTitle>
          <div>
            {ROUTINES.map((r) => (
              <div key={r.name} className="flex items-center justify-between py-2 text-[14px]">
                <span className="flex items-center gap-2.5 text-foreground/90">
                  <ClockDotIcon size={14} className="text-warning" />
                  {r.name}
                </span>
                <span className="text-[12.5px] text-muted-foreground">{r.when}</span>
              </div>
            ))}
            <div className="py-2 text-[13.5px] text-muted-foreground">+ New routine</div>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
