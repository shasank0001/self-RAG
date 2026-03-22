import { streamPost } from "@/lib/api/client";
import type { StreamEnvelope, StreamEventName } from "@/types/chat";

export type SSEEvent = {
  id: string;
  event: string;
  data: string;
};

export type SSEHandlers = {
  onEvent: (event: SSEEvent) => void;
  onUnknownEvent?: (event: SSEEvent) => void;
};

function toLines(buffer: string): { lines: string[]; rest: string } {
  const parts = buffer.split("\n");
  const rest = parts.pop() ?? "";
  return { lines: parts, rest };
}

function parseEventFrame(frame: string): SSEEvent | null {
  if (!frame.trim()) {
    return null;
  }

  let event = "message";
  let id = "";
  const dataParts: string[] = [];
  const lines = frame.split("\n");
  for (const line of lines) {
    if (!line || line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    if (colon < 0) {
      continue;
    }
    const field = line.slice(0, colon).trim();
    const value = line.slice(colon + 1).replace(/^\s/, "");
    if (field === "event") {
      event = value;
    } else if (field === "id") {
      id = value;
    } else if (field === "data") {
      dataParts.push(value);
    }
  }

  return {
    id,
    event,
    data: dataParts.join("\n"),
  };
}

export async function parseSSEStream(
  stream: ReadableStream<Uint8Array>,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let textBuffer = "";
  let frameBuffer = "";

  try {
    while (true) {
      if (signal?.aborted) {
        await reader.cancel();
        break;
      }

      const { value, done } = await reader.read();
      if (done) {
        break;
      }

      textBuffer += decoder.decode(value, { stream: true });
      const { lines, rest } = toLines(textBuffer);
      textBuffer = rest;

      for (const line of lines) {
        if (line === "") {
          const parsed = parseEventFrame(frameBuffer);
          frameBuffer = "";
          if (!parsed) {
            continue;
          }
          handlers.onEvent(parsed);
        } else {
          frameBuffer = frameBuffer ? `${frameBuffer}\n${line}` : line;
        }
      }
    }

    const trailing = frameBuffer.trim();
    if (trailing) {
      const parsed = parseEventFrame(trailing);
      if (parsed) {
        handlers.onEvent(parsed);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export function decodeStreamEnvelope<T>(event: SSEEvent): StreamEnvelope<T> {
  return JSON.parse(event.data) as StreamEnvelope<T>;
}

export async function openSSE(
  path: string,
  payload: unknown,
  handlers: SSEHandlers,
  signal?: AbortSignal,
  headers?: Record<string, string>,
): Promise<void> {
  const stream = await streamPost(path, payload, { signal, headers });
  if (!stream) {
    throw new Error("Stream body is unavailable");
  }
  await parseSSEStream(stream, handlers, signal);
}

export function isKnownStreamEvent(name: string): name is StreamEventName {
  return (
    name === "thinking" ||
    name === "token" ||
    name === "citations" ||
    name === "done" ||
    name === "heartbeat" ||
    name === "error"
  );
}
