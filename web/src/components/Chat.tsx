/* Center pane: header (avatar + title + status pill), transcript, composer. */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { colorFor } from "../format.ts";
import type { ChatItem, LabState } from "../state.ts";
import type { LabRow } from "../types.ts";
import { BotAvatar } from "./BotAvatar.tsx";
import {
  AgentBubble,
  AskCard,
  ChampionCard,
  DoneLine,
  HypothesisBubble,
  LaunchLine,
  ResultsCard,
  SealCard,
  StagnationCard,
  TimeDivider,
  UserBubble,
} from "./cards.tsx";
import { ArrowUpIcon, MenuIcon, MonitorIcon } from "./icons.tsx";

function StatusPill({ status, connection }: { status: LabState["status"]; connection: "idle" | "open" | "error" }) {
  if (connection === "error")
    return <span className="rounded-full bg-destructive/15 px-[11px] py-1 text-[13px] text-destructive">Reconnecting…</span>;
  switch (status) {
    case "waiting":
      return <span className="rounded-full bg-warning/15 px-[11px] py-1 text-[13px] text-warning">Needs you</span>;
    case "running":
      return (
        <span className="flex items-center gap-1.5 rounded-full bg-success/15 px-[11px] py-1 text-[13px] text-success">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-success" style={{ animation: "rkPulse 1.6s ease-in-out infinite" }} />
          Researching
        </span>
      );
    case "done":
      return <span className="rounded-full bg-accent px-[11px] py-1 text-[13px] text-muted-foreground">Champion found</span>;
    default:
      return <span className="rounded-full bg-accent px-[11px] py-1 text-[13px] text-muted-foreground">Idle</span>;
  }
}

function Item({
  item,
  state,
  onAnswer,
}: {
  item: ChatItem;
  state: LabState;
  onAnswer: (askId: string, optionId: string) => Promise<void>;
}) {
  switch (item.kind) {
    case "divider":
      return <TimeDivider ts={item.ts} />;
    case "agent":
      return <AgentBubble text={item.text} cards={item.cards} />;
    case "user":
      return <UserBubble text={item.text} />;
    case "hypothesis":
      return <HypothesisBubble hyp={item.hyp} />;
    case "launch":
      return <LaunchLine count={item.ids.length} runner={item.runner} lane={item.lane} />;
    case "results":
      return <ResultsCard rows={item.rows} />;
    case "seal":
      return <SealCard ev={item.ev} />;
    case "stagnation":
      return <StagnationCard ev={item.ev} />;
    case "champion":
      return <ChampionCard ev={item.ev} />;
    case "ask": {
      const ask = state.asks[item.ev.id];
      return (
        <AskCard
          ev={item.ev}
          answered={ask?.answered ?? null}
          canAnswer={state.openAskId === item.ev.id && state.status !== "done"}
          onAnswer={(opt) => onAnswer(item.ev.id, opt)}
        />
      );
    }
    case "done":
      return <DoneLine summary={item.summary} />;
  }
}

export function Chat({
  lab,
  state,
  connection,
  panelOpen,
  onTogglePanel,
  onOpenSidebar,
  onSend,
  onAnswer,
}: {
  lab: LabRow | null;
  state: LabState;
  connection: "idle" | "open" | "error";
  panelOpen: boolean;
  onTogglePanel: () => void;
  onOpenSidebar: () => void;
  onSend: (text: string) => Promise<void>;
  onAnswer: (askId: string, optionId: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  // Follow the transcript while the reader is at the bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [state.items]);

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  async function submit() {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setSendError(null);
    try {
      await onSend(text);
      setDraft("");
      stickRef.current = true;
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "Could not send");
    } finally {
      setSending(false);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  }

  const working = state.status === "running" || state.status === "waiting";
  const title = lab?.title ?? (state.task ? `${state.task} · composite` : "Airlock");

  return (
    <main className="flex min-w-0 flex-1 flex-col bg-background">
      <div className="flex items-center justify-between border-b border-sidebar-border px-3 py-[17px] md:px-[22px]">
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            aria-label="Open navigation"
            onClick={onOpenSidebar}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-foreground/75 hover:bg-accent md:hidden"
          >
            <MenuIcon size={19} strokeWidth={1.7} />
          </button>
          <div className="flex min-w-0 items-center gap-3">
            {lab ? <BotAvatar color={colorFor(lab.id, lab.color)} identity={lab.id} size={26} working={working} /> : null}
            <span className="min-w-0">
              <span className="block truncate text-[16px] font-medium text-foreground" dir="auto">
                {lab ? title : "Select a lab"}
              </span>
            </span>
            {lab ? <StatusPill status={state.status} connection={connection} /> : null}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            title="Airlock's computer"
            aria-pressed={panelOpen}
            onClick={onTogglePanel}
            data-active={panelOpen ? "" : undefined}
            className="grid h-[30px] w-[34px] place-items-center rounded-[9px] text-foreground/75 hover:bg-accent data-active:bg-accent data-active:text-foreground"
          >
            <MonitorIcon size={17} strokeWidth={1.7} />
          </button>
        </div>
      </div>

      <div ref={scrollRef} onScroll={onScroll} className="rk-scroll min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-6">
        <div className="mx-auto flex w-full max-w-[820px] flex-col gap-3">
          {!lab ? (
            <div className="flex flex-col items-center gap-3 py-24 text-center">
              <div className="text-[19px] font-medium text-foreground">Start a lab</div>
              <div className="max-w-[420px] text-[14.5px] leading-[1.5] text-muted-foreground">
                Describe the task and Airlock screens the catalog, confirms one axis at a time, seals dead families,
                and only runs the cheapest experiment that can falsify its leading explanation.
              </div>
            </div>
          ) : state.items.length === 0 ? (
            <div className="py-16 text-center text-[13.5px] text-muted-foreground/70">
              {connection === "error" ? "Waiting for the engine…" : "Connecting…"}
            </div>
          ) : (
            state.items.map((item) => <Item key={item.id} item={item} state={state} onAnswer={onAnswer} />)
          )}
        </div>
      </div>

      <div className="px-3 pb-4 pt-2 md:px-6">
        <div className="mx-auto w-full max-w-[820px]">
          {sendError ? <p className="mb-1.5 text-[12.5px] text-destructive">{sendError}</p> : null}
          <div
            data-testid="composer-bar"
            className="flex items-end gap-3.5 rounded-xl border border-border bg-card py-[9px] pe-2.5 ps-3.5 transition-colors focus-within:border-ring"
          >
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKey}
              rows={1}
              name="chat-message"
              autoComplete="off"
              dir="auto"
              disabled={sending}
              placeholder={
                lab
                  ? state.status === "done"
                    ? "Ask why something failed, or start a new lab…"
                    : "Steer: focus family ensemble · why did X fail · run 5 more · stop"
                  : "Describe the task to start a lab…"
              }
              className="max-h-32 min-h-[24px] min-w-[8rem] flex-1 resize-none overflow-y-auto bg-transparent py-0.5 text-[15.5px] leading-6 text-foreground outline-none placeholder:text-muted-foreground disabled:opacity-40"
            />
            <button
              type="button"
              aria-label="Send"
              disabled={!draft.trim() || sending}
              onClick={() => void submit()}
              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground transition-all hover:bg-primary/80 disabled:opacity-40"
            >
              <ArrowUpIcon size={16} strokeWidth={2.2} />
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
