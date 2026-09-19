/* Fitness per round: best line, mean line, worst→best band, best-single
   reference. One y-axis, thin marks, crosshair tooltip on hover. */
import { useId, useState } from "react";
import type { ProgressPoint } from "../types.ts";

const W = 328;
const H = 156;
const PAD = { l: 38, r: 10, t: 10, b: 22 };

export function FitnessChart({
  history,
  bestSingle,
  metric,
}: {
  history: ProgressPoint[];
  bestSingle: number | null;
  metric: string;
}) {
  const id = useId().replace(/[^a-zA-Z0-9-_]/g, "");
  const [hover, setHover] = useState<number | null>(null);
  if (history.length === 0) {
    return (
      <div className="grid h-[156px] place-items-center text-[12.5px] text-muted-foreground/70">
        Waiting for the first round…
      </div>
    );
  }
  const rounds = history.map((p) => p.round);
  const xMin = Math.min(...rounds);
  const xMax = Math.max(...rounds, xMin + 1);
  const vals = history.flatMap((p) => [p.best, p.mean, p.worst]).concat(bestSingle !== null ? [bestSingle] : []);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = Math.max(hi - lo, 0.01);
  const yMin = lo - span * 0.15;
  const yMax = hi + span * 0.15;
  const x = (r: number) => PAD.l + ((r - xMin) / (xMax - xMin)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.t - PAD.b);

  const line = (key: "best" | "mean" | "worst") => history.map((p, i) => `${i ? "L" : "M"}${x(p.round).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const band =
    history.map((p, i) => `${i ? "L" : "M"}${x(p.round).toFixed(1)},${y(p.best).toFixed(1)}`).join(" ") +
    " " +
    [...history].reverse().map((p) => `L${x(p.round).toFixed(1)},${y(p.worst).toFixed(1)}`).join(" ") +
    " Z";

  const ticks = 3;
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) => yMin + ((yMax - yMin) * i) / ticks);
  const hp = hover !== null ? history[hover] : null;

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    let best = 0;
    let bd = Infinity;
    history.forEach((p, i) => {
      const d = Math.abs(x(p.round) - px);
      if (d < bd) {
        bd = d;
        best = i;
      }
    });
    setHover(best);
  }

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-label={`${metric} per round: best, mean and worst`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        className="block"
      >
        <defs>
          <linearGradient id={`${id}-band`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--link)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--link)" stopOpacity="0.04" />
          </linearGradient>
        </defs>
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeWidth="1" />
            <text x={PAD.l - 6} y={y(v) + 3.5} textAnchor="end" fontSize="9.5" fill="var(--muted-foreground)" className="tabular-nums">
              {v.toFixed(3)}
            </text>
          </g>
        ))}
        {history.map((p) => (
          <text key={p.round} x={x(p.round)} y={H - 7} textAnchor="middle" fontSize="9.5" fill="var(--muted-foreground)">
            {p.round}
          </text>
        ))}
        <path d={band} fill={`url(#${id}-band)`} stroke="none" />
        {bestSingle !== null ? (
          <line x1={PAD.l} x2={W - PAD.r} y1={y(bestSingle)} y2={y(bestSingle)} stroke="var(--muted-foreground)" strokeWidth="1" strokeDasharray="2 3" />
        ) : null}
        <path d={line("worst")} fill="none" stroke="var(--link)" strokeOpacity="0.35" strokeWidth="1.25" />
        <path d={line("mean")} fill="none" stroke="var(--muted-foreground)" strokeWidth="1.5" strokeDasharray="4 3" />
        <path d={line("best")} fill="none" stroke="var(--link)" strokeWidth="2" strokeLinejoin="round" />
        {history.map((p) => (
          <circle key={p.round} cx={x(p.round)} cy={y(p.best)} r={hover !== null && history[hover] === p ? 4 : 2.5} fill="var(--link)" stroke="var(--card)" strokeWidth="2" />
        ))}
        {hp ? <line x1={x(hp.round)} x2={x(hp.round)} y1={PAD.t} y2={H - PAD.b} stroke="var(--foreground)" strokeOpacity="0.25" strokeWidth="1" /> : null}
      </svg>
      {hp ? (
        <div
          className="pointer-events-none absolute top-1 rounded-lg border border-border bg-popover px-2.5 py-1.5 text-[11.5px] tabular-nums text-foreground shadow-md"
          style={{ left: `${Math.min(78, Math.max(2, (x(hp.round) / W) * 100))}%`, transform: "translateX(-50%)" }}
        >
          <div className="text-muted-foreground">round {hp.round}</div>
          <div>best {hp.best.toFixed(3)}</div>
          <div className="text-muted-foreground">mean {hp.mean.toFixed(3)} · worst {hp.worst.toFixed(3)}</div>
        </div>
      ) : null}
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="h-[2px] w-3 rounded bg-link" /> best</span>
        <span className="flex items-center gap-1.5"><span className="h-[2px] w-3 rounded border-t border-dashed border-muted-foreground" /> mean</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-3 rounded-[2px] bg-link/20" /> worst → best</span>
        {bestSingle !== null ? <span className="flex items-center gap-1.5"><span className="h-[1px] w-3 border-t border-dotted border-muted-foreground" /> best single</span> : null}
      </div>
    </div>
  );
}
