import type { RuntimeEvent } from "./types";

export interface SseHandle {
  close: () => void;
}

/**
 * Subscribe to run events. The browser EventSource automatically resumes with
 * `Last-Event-ID` after a dropped connection; the server replays from the cursor,
 * and authoritative state always comes from the run API.
 */
export function subscribeEvents(
  runId: string,
  handlers: {
    onEvent: (event: RuntimeEvent) => void;
    onTerminal: () => void;
    onError: () => void;
  },
): SseHandle {
  const source = new EventSource(`/api/runs/${runId}/events?follow=true`);
  source.onmessage = (message: MessageEvent<string>) => {
    // Named events carry the payload; default handler is a safety net.
    handleData(message.data, handlers);
  };
  const forward = (message: MessageEvent<string>) => handleData(message.data, handlers);
  for (const kind of [
    "RUN_CREATED",
    "PROGRESS",
    "STATUS",
    "COUNTEREXAMPLE",
    "LEAKAGE_BLOCKED",
  ]) {
    source.addEventListener(kind, forward as EventListener);
  }
  source.onerror = () => {
    // EventSource retries automatically; surface the hiccup for the UI.
    handlers.onError();
  };
  return { close: () => source.close() };
}

function handleData(
  data: string,
  handlers: { onEvent: (event: RuntimeEvent) => void; onTerminal: () => void },
): void {
  try {
    const parsed = JSON.parse(data) as RuntimeEvent;
    if (parsed && typeof parsed === "object" && "event_type" in parsed) {
      handlers.onEvent(parsed);
    }
  } catch {
    // Heartbeats and empty frames are not JSON; ignore.
  }
  void handlers.onTerminal;
}
