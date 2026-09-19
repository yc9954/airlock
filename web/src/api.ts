/* HTTP + SSE client for the Python engine (SPEC §2 HTTP API), and the ?mock=1
   switch that swaps in the canned stream from src/mock. */
import { MockLabSource, mockLabs } from "./mock/source.ts";
import { type AirlockEvent, EVENT_TYPES, type EventType, type LabRow } from "./types.ts";

export function isMock(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("mock") === "1";
  } catch {
    return false;
  }
}

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${init?.method ?? "GET"} ${input} → ${res.status}`);
  return (await res.json()) as T;
}

export async function listLabs(): Promise<LabRow[]> {
  if (isMock()) return mockLabs();
  const body = await json<LabRow[] | { labs: LabRow[] }>("/api/labs");
  return Array.isArray(body) ? body : body.labs;
}

export async function createLab(prompt: string): Promise<string> {
  if (isMock()) return MockLabSource.create(prompt);
  const body = await json<{ lab_id: string }>("/api/labs", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
  return body.lab_id;
}

export async function steerLab(labId: string, text: string): Promise<void> {
  if (isMock()) {
    MockLabSource.steer(labId, text);
    return;
  }
  await json<unknown>(`/api/labs/${encodeURIComponent(labId)}/steer`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export async function answerAsk(labId: string, askId: string, optionId: string): Promise<void> {
  if (isMock()) {
    MockLabSource.answer(labId, askId, optionId);
    return;
  }
  await json<unknown>(`/api/labs/${encodeURIComponent(labId)}/answer`, {
    method: "POST",
    body: JSON.stringify({ ask_id: askId, option_id: optionId }),
  });
}

/** Parse one SSE payload. Accepts `{"type": ...}` (the broker's framing) and
 *  `event`/`kind` as fallbacks. Returns null for anything not in the union. */
export function parseEvent(raw: string, namedType?: string): AirlockEvent | null {
  let obj: unknown;
  try {
    obj = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!obj || typeof obj !== "object") return null;
  const rec = obj as Record<string, unknown>;
  const t = (rec.type ?? rec.event ?? rec.kind ?? namedType) as string | undefined;
  if (!t || !(EVENT_TYPES as readonly string[]).includes(t)) return null;
  return { ...rec, type: t as EventType } as AirlockEvent;
}

export interface StreamHandle {
  close(): void;
}

/** Open the lab's event stream. Replays from 0 on connect (server contract). */
export function openStream(
  labId: string,
  onEvent: (event: AirlockEvent) => void,
  onStatus?: (s: "open" | "error") => void,
): StreamHandle {
  if (isMock()) return MockLabSource.open(labId, onEvent, onStatus);
  const es = new EventSource(`/api/labs/${encodeURIComponent(labId)}/stream`);
  const handle = (raw: string, named?: string) => {
    const ev = parseEvent(raw, named);
    if (ev) onEvent(ev);
  };
  es.onmessage = (m: MessageEvent<string>) => handle(m.data);
  for (const name of EVENT_TYPES) {
    es.addEventListener(name, (m: Event) => {
      const data = (m as MessageEvent<string>).data;
      if (typeof data === "string") handle(data, name);
    });
  }
  es.onopen = () => onStatus?.("open");
  es.onerror = () => onStatus?.("error");
  return { close: () => es.close() };
}
