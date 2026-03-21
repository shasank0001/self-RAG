import { useCallback, useRef } from "react";

import { decodeStreamEnvelope, isKnownStreamEvent, openSSE } from "@/lib/stream/sse";
import type {
  Citation,
  StreamDonePayload,
  StreamEnvelope,
  StreamErrorPayload,
  StreamEventName,
} from "@/types/chat";

type SSEHandlerMap = {
  onToken?: (payload: { text: string }, cursor: string) => void;
  onCitations?: (payload: { citations: Citation[]; retrieval_mode: string }, cursor: string) => void;
  onDone?: (payload: StreamDonePayload, cursor: string) => void;
  onHeartbeat?: (payload: { status: string }, cursor: string) => void;
  onError?: (payload: StreamErrorPayload, cursor: string) => void;
  onUnknown?: (event: { event: string; raw: string }) => void;
};

function parseEnvelope<T>(raw: string): StreamEnvelope<T> {
  return JSON.parse(raw) as StreamEnvelope<T>;
}

export function useSSE() {
  const controllerRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  const connect = useCallback(
    async (
      path: string,
      payload: unknown,
      handlers: SSEHandlerMap,
      options?: { cursor?: string | null; signal?: AbortSignal },
    ) => {
      cancel();
      const controller = new AbortController();
      controllerRef.current = controller;

      const upstreamSignal = options?.signal;
      if (upstreamSignal) {
        upstreamSignal.addEventListener("abort", () => controller.abort(), { once: true });
      }

      await openSSE(
        path,
        payload,
        {
          onEvent: (event) => {
            if (!isKnownStreamEvent(event.event)) {
              handlers.onUnknown?.({ event: event.event, raw: event.data });
              return;
            }

            const knownEvent = event.event as StreamEventName;
            if (knownEvent === "token") {
              const envelope = parseEnvelope<{ text: string }>(event.data);
              handlers.onToken?.(envelope.data, envelope.id);
              return;
            }
            if (knownEvent === "citations") {
              const envelope = decodeStreamEnvelope<{ citations: Citation[]; retrieval_mode: string }>(event);
              handlers.onCitations?.(envelope.data, envelope.id);
              return;
            }
            if (knownEvent === "done") {
              const envelope = decodeStreamEnvelope<StreamDonePayload>(event);
              handlers.onDone?.(envelope.data, envelope.id);
              return;
            }
            if (knownEvent === "heartbeat") {
              const envelope = decodeStreamEnvelope<{ status: string }>(event);
              handlers.onHeartbeat?.(envelope.data, envelope.id);
              return;
            }
            if (knownEvent === "error") {
              const envelope = decodeStreamEnvelope<StreamErrorPayload>(event);
              handlers.onError?.(envelope.data, envelope.id);
            }
          },
          onUnknownEvent: (event) => {
            handlers.onUnknown?.({ event: event.event, raw: event.data });
          },
        },
        controller.signal,
        options?.cursor ? { "Last-Event-ID": options.cursor } : undefined,
      );
    },
    [cancel],
  );

  return {
    connect,
    cancel,
  };
}
