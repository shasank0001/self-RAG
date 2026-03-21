import { create } from "zustand";

import type { Citation, StreamDonePayload, StreamErrorPayload } from "@/types/chat";

export type ConnectionStatus = "idle" | "connecting" | "streaming" | "reconnecting" | "offline" | "error";

type ChatStore = {
  status: ConnectionStatus;
  draftAssistantText: string;
  cursor: string | null;
  citations: Citation[];
  retryCount: number;
  lastError: StreamErrorPayload | null;
  activeSessionId: string | null;
  setStatus: (status: ConnectionStatus) => void;
  setActiveSession: (sessionId: string | null) => void;
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
  retryCount: 0,
  lastError: null as StreamErrorPayload | null,
};

export const useChatStore = create<ChatStore>((set) => ({
  ...initialTransient,
  activeSessionId: null,
  setStatus: (status) => set({ status }),
  setActiveSession: (sessionId) => set({ activeSessionId: sessionId }),
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
      cursor: cursor ?? state.cursor,
      retryCount: 0,
      lastError: null,
    })),
  markError: (payload) =>
    set(() => ({
      status: "error",
      lastError: payload,
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
}));
