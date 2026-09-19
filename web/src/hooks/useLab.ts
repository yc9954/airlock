/* useLabs: sidebar list polled every 3 s. useLab: one lab's SSE stream folded
   through the reducer. */
import { useCallback, useEffect, useReducer, useState } from "react";
import { listLabs, openStream } from "../api.ts";
import { type Action, initialState, type LabState, reduce } from "../state.ts";
import type { LabRow } from "../types.ts";

export const LABS_POLL_MS = 3000;

export function useLabs(): { labs: LabRow[]; refresh: () => Promise<void>; error: string | null } {
  const [labs, setLabs] = useState<LabRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      const rows = await listLabs();
      setLabs(rows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not list labs");
    }
  }, []);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      await refresh();
      if (!cancelled) timer = window.setTimeout(() => void tick(), LABS_POLL_MS);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [refresh]);
  return { labs, refresh, error };
}

export function useLab(labId: string | null): {
  state: LabState;
  dispatch: (a: Action) => void;
  connection: "idle" | "open" | "error";
} {
  const [state, dispatch] = useReducer(reduce, undefined, initialState);
  const [connection, setConnection] = useState<"idle" | "open" | "error">("idle");
  useEffect(() => {
    dispatch({ type: "reset" });
    setConnection("idle");
    if (!labId) return;
    const handle = openStream(
      labId,
      (event) => dispatch({ type: "event", event, receivedAt: Date.now() }),
      (s) => setConnection(s),
    );
    return () => handle.close();
  }, [labId]);
  return { state, dispatch, connection };
}
