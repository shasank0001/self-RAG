import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type { Citation, StreamDonePayload, StreamErrorPayload, ThinkingStep } from "@/types/chat";

export type ConnectionStatus = "idle" | "connecting" | "streaming" | "reconnecting" | "offline" | "error";

type ChatStore = {
  status: ConnectionStatus;
  draftAssistantText: string;
  cursor: string | null;
  citations: Citation[];
  thinkingSteps: ThinkingStep[];
  retryCount: number;
  lastError: StreamErrorPayload | null;
  activeSessionId: string | null;
  setStatus: (status: ConnectionStatus) => void;
  setActiveSession: (sessionId: string | null) => void;
  upsertThinkingStep: (step: ThinkingStep, cursor: string | null) => void;
  appendDraft: (chunk: string, cursor: string | null) => void;
  setCitations: (citations: Citation[], cursor: string | null) => void;
  markDone: (payload: StreamDonePayload, cursor: string | null) => void;
  markError: (payload: StreamErrorPayload) => void;
  incrementRetry: () => void;
  resetStreamState: () => void;
};

const initialTransient = {
  status: "idle" as ConnectionStatus,
  draftAssistantText: "",
  cursor: null as string | null,
  citations: [] as Citation[],
  thinkingSteps: [] as ThinkingStep[],
  retryCount: 0,
  lastError: null as StreamErrorPayload | null,
};

export const useChatStore = create<ChatStore>()(
  persist(
    (set) => ({
      ...initialTransient,
      activeSessionId: null,
      setStatus: (status) => set({ status }),
      setActiveSession: (sessionId) => set({ activeSessionId: sessionId }),
      upsertThinkingStep: (step, cursor) =>
        set((state) => {
          const index = state.thinkingSteps.findIndex((item) => item.step_id === step.step_id);
          const thinkingSteps =
            index >= 0
              ? state.thinkingSteps.map((item, itemIndex) => (itemIndex === index ? { ...item, ...step } : item))
              : [...state.thinkingSteps, step];

          return {
            thinkingSteps,
            cursor: cursor ?? state.cursor,
            status: "streaming",
            lastError: null,
          };
        }),
      appendDraft: (chunk, cursor) =>
        set((state) => ({
          draftAssistantText: `${state.draftAssistantText}${chunk}`,
          cursor: cursor ?? state.cursor,
          status: "streaming",
          lastError: null,
        })),
      setCitations: (citations, cursor) =>
        set((state) => ({
          citations,
          cursor: cursor ?? state.cursor,
          lastError: null,
        })),
      markDone: (_payload, cursor) =>
        set((state) => ({
          status: "idle",
          draftAssistantText: "",
          citations: [],
          thinkingSteps: [],
          // The terminal `done` cursor cannot be used for stream resume.
          cursor: null,
          retryCount: 0,
          lastError: null,
        })),
      markError: (payload) =>
        set((state) => ({
          status: "error",
          lastError: payload,
          cursor: payload.code === "cursor_invalid" ? null : state.cursor,
        })),
      incrementRetry: () =>
        set((state) => ({
          retryCount: state.retryCount + 1,
          status: "reconnecting",
        })),
      resetStreamState: () =>
        set((state) => ({
          ...initialTransient,
          activeSessionId: state.activeSessionId,
        })),
    }),
    {
      name: "self-rag-chat-store",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        activeSessionId: state.activeSessionId,
      }),
    },
  ),
);
