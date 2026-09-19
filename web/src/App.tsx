/* rakazo Shell: sidebar · chat · Airlock's computer. */
import { useCallback, useEffect, useState } from "react";
import { answerAsk, createLab, isMock, steerLab } from "./api.ts";
import { Chat } from "./components/Chat.tsx";
import { Panel } from "./components/Panel.tsx";
import { Sidebar } from "./components/Sidebar.tsx";
import { useLab, useLabs } from "./hooks/useLab.ts";

function prefersPanelOpen(): boolean {
  try {
    return window.matchMedia("(min-width: 768px)").matches;
  } catch {
    return true;
  }
}

export default function App() {
  const { labs, refresh } = useLabs();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [panelOpen, setPanelOpen] = useState(prefersPanelOpen);
  const { state, dispatch, connection } = useLab(activeId);

  // Open the first lab once the list arrives (rakazo opens a bot by default).
  useEffect(() => {
    if (activeId === null && labs.length > 0) {
      const preferred = isMock() ? (labs.find((l) => l.id === "lab_bc") ?? labs[0]) : labs[0];
      if (preferred) setActiveId(preferred.id);
    }
  }, [labs, activeId]);

  const active = labs.find((l) => l.id === activeId) ?? null;

  const select = useCallback((id: string) => {
    setActiveId(id);
    setMobileSidebar(false);
  }, []);

  const startLab = useCallback(
    async (prompt: string) => {
      const id = await createLab(prompt);
      dispatch({ type: "reset" });
      setActiveId(id);
      setMobileSidebar(false);
      await refresh();
    },
    [dispatch, refresh],
  );

  const send = useCallback(
    async (text: string) => {
      if (!activeId || state.status === "done") {
        await startLab(text);
        return;
      }
      dispatch({ type: "user", text, ts: Math.floor(Date.now() / 1000) });
      await steerLab(activeId, text);
    },
    [activeId, state.status, startLab, dispatch],
  );

  const answer = useCallback(
    async (askId: string, optionId: string) => {
      if (!activeId) return;
      await answerAsk(activeId, askId, optionId);
      dispatch({ type: "answered", askId, optionId });
    },
    [activeId, dispatch],
  );

  const newLab = useCallback(() => {
    setActiveId(null);
    dispatch({ type: "reset" });
    setMobileSidebar(false);
  }, [dispatch]);

  return (
    <div className="relative flex h-full w-full overflow-hidden bg-background text-foreground">
      {mobileSidebar ? (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setMobileSidebar(false)}
          className="absolute inset-y-0 end-0 start-[min(calc(100%-48px),316px)] z-30 bg-overlay md:hidden"
        />
      ) : null}
      <Sidebar labs={labs} activeId={activeId} mobileOpen={mobileSidebar} onSelect={select} onNew={newLab} />
      <Chat
        lab={active}
        state={state}
        connection={connection}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen((o) => !o)}
        onOpenSidebar={() => setMobileSidebar(true)}
        onSend={send}
        onAnswer={answer}
      />
      <Panel state={state} open={panelOpen && activeId !== null} onClose={() => setPanelOpen(false)} />
    </div>
  );
}
