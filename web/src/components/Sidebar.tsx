/* Left pane — rakazo's bots sidebar: window chrome + '+', search box, one row
   per lab (avatar, name, time, preview, active dot). Collapses off-canvas on
   mobile. */
import { useMemo, useState } from "react";
import { colorFor, fmtRosterTime } from "../format.ts";
import type { LabRow } from "../types.ts";
import { BotAvatar } from "./BotAvatar.tsx";
import { PlusIcon, SearchIcon } from "./icons.tsx";

export function isWorking(status: string | undefined): boolean {
  return status === "running" || status === "waiting" || status === "starting";
}

export function Sidebar({
  labs,
  activeId,
  mobileOpen,
  onSelect,
  onNew,
}: {
  labs: LabRow[];
  activeId: string | null;
  mobileOpen: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const rows = useMemo(
    () => (needle ? labs.filter((l) => l.title.toLowerCase().includes(needle) || l.last_message.toLowerCase().includes(needle)) : labs),
    [labs, needle],
  );

  return (
    <aside
      data-testid="labs-sidebar"
      className={`absolute inset-y-0 start-0 z-40 flex w-[calc(100%-48px)] max-w-[316px] shrink-0 flex-col border-e border-sidebar-border bg-sidebar transition-[transform,width,opacity] md:static md:z-auto md:w-[316px] md:translate-x-0 ${
        mobileOpen ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <div className="flex items-center justify-between px-[18px] pb-3 pt-4">
        <div className="flex items-center gap-2" aria-hidden="true">
          <span className="h-3 w-3 rounded-full bg-[#ff5f57]" />
          <span className="h-3 w-3 rounded-full bg-[#febc2e]" />
          <span className="h-3 w-3 rounded-full bg-[#28c840]" />
        </div>
        <button
          type="button"
          aria-label="New lab"
          title="New lab"
          onClick={onNew}
          className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
        >
          <PlusIcon size={16} />
        </button>
      </div>

      <label
        data-testid="sidebar-search"
        className="mx-2.5 mb-3 flex items-center gap-2 rounded-xl border border-border bg-input px-3 py-[7px] text-muted-foreground focus-within:border-ring"
      >
        <SearchIcon size={16} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search"
          aria-label="Search labs"
          className="min-w-0 flex-1 bg-transparent text-[14px] text-foreground outline-none placeholder:text-muted-foreground"
        />
      </label>

      <div className="rk-scroll min-h-0 flex-1 overflow-y-auto px-2.5 pb-3">
        {rows.length === 0 ? (
          <div className="px-2.5 py-6 text-center text-[13px] text-muted-foreground/70">
            {labs.length === 0 ? "No labs yet — start one below." : "No labs match"}
          </div>
        ) : null}
        {rows.map((lab) => {
          const active = lab.id === activeId;
          const working = isWorking(lab.status);
          return (
            <button
              key={lab.id}
              type="button"
              onClick={() => onSelect(lab.id)}
              data-active={active ? "" : undefined}
              className={`flex w-full items-center gap-3 rounded-xl px-2.5 py-[10px] text-start ${
                active ? "bg-sidebar-accent" : "hover:bg-sidebar-accent"
              }`}
            >
              <BotAvatar color={colorFor(lab.id, lab.color)} identity={lab.id} size={38} working={working} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-1.5">
                  <span className={`min-w-0 truncate text-[14px] text-foreground ${working ? "font-semibold" : "font-medium"}`}>
                    {lab.title}
                  </span>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span className="text-[11.5px] tabular-nums text-muted-foreground/60">{fmtRosterTime(lab.updated)}</span>
                    {working ? (
                      <span
                        aria-label="active"
                        className="inline-block h-2 w-2 rounded-full bg-success"
                        style={{ animation: "rkPulse 1.6s ease-in-out infinite" }}
                      />
                    ) : null}
                  </div>
                </div>
                <div
                  className={`mt-1 line-clamp-2 whitespace-normal break-words text-[12.5px] ${
                    working ? "font-medium text-foreground/75" : "text-muted-foreground/60"
                  }`}
                >
                  {lab.last_message}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-[11px] border-t border-sidebar-border px-[18px] py-3.5">
        <span className="grid h-8 w-8 place-items-center rounded-full bg-accent text-[12px] text-foreground/75">A</span>
        <span className="min-w-0">
          <span className="block text-[14.5px] text-foreground/90">Airlock</span>
          <span className="block text-[11.5px] text-muted-foreground/60">Selection pressure, not agent spam.</span>
        </span>
      </div>
    </aside>
  );
}
