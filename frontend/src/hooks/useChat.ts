import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { apiFetch, apiUpload } from "@/lib/api/client";
import { useSSE } from "@/hooks/useSSE";
import { useChatStore } from "@/store/chatStore";
import type {
  BinRecord,
  Citation,
  IngestionAcceptedResponse,
  MessageRecord,
  SessionListItem,
  SessionRecord,
  StreamDonePayload,
  StreamErrorPayload,
  ThinkingStep,
} from "@/types/chat";

type SendMessageArgs = {
  sessionId: string;
  message: string;
  binIds: string[];
  cursor?: string | null;
};

type UpdateSessionBinsArgs = {
  sessionId: string;
  binIds: string[];
};

const RETRY_MAX = Number(import.meta.env.CHAT_STREAM_RETRY_MAX_ATTEMPTS ?? "5");
const RETRY_BASE_MS = Number(import.meta.env.CHAT_STREAM_RETRY_BASE_MS ?? "500");
const mockMode = import.meta.env.VITE_MOCK_API === "true";

const mockBins = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    title: "Policy Bin",
    description: "HR and compliance documents",
    vector_namespace: "ns-policy",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    title: "Engineering Bin",
    description: "Architecture notes and runbooks",
    vector_namespace: "ns-engineering",
  },
  {
    id: "33333333-3333-3333-3333-333333333333",
    title: "Product Bin",
    description: "Roadmap, PRDs, and release notes",
    vector_namespace: "ns-product",
  },
];

const mockSessionA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const mockSessionB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

const mockSessions: SessionRecord[] = [
  {
    id: mockSessionA,
    user_id: "00000000-0000-0000-0000-000000000001",
    title: "Policy Q&A",
    initial_bin_ids: [mockBins[0].id],
    last_active_bin_ids: [mockBins[0].id, mockBins[1].id],
  },
  {
    id: mockSessionB,
    user_id: "00000000-0000-0000-0000-000000000001",
    title: "Product handoff prep",
    initial_bin_ids: [mockBins[2].id],
    last_active_bin_ids: [mockBins[2].id],
  },
];

const mockMessagesBySession: Record<string, MessageRecord[]> = {
  [mockSessionA]: [
    {
      id: "m-user-1",
      session_id: mockSessionA,
      user_id: "00000000-0000-0000-0000-000000000001",
      role: "user",
      content: "What obligations are listed in the policy?",
      bin_ids_used: [mockBins[0].id],
      retrieval_mode: null,
      citations: [],
      provider_metadata: {},
      prompt_versions: {},
    },
    {
      id: "m-assistant-1",
      session_id: mockSessionA,
      user_id: "00000000-0000-0000-0000-000000000001",
      role: "assistant",
      content: "The policy requires annual review, notice delivery within 30 days, and explicit renewal confirmation.",
      bin_ids_used: [mockBins[0].id],
      retrieval_mode: "grounded",
      citations: [
        {
          item_name: "policy_v3.pdf",
          chunk_excerpt: "Section 3 states annual review and 30-day notice requirements.",
          bin_title: "Policy Bin",
          chunk_id: "policy_chunk_1",
          score: 0.94,
        },
      ],
      provider_metadata: {},
      prompt_versions: { answer_generator: "v1" },
    },
  ],
  [mockSessionB]: [
    {
      id: "m-user-2",
      session_id: mockSessionB,
      user_id: "00000000-0000-0000-0000-000000000001",
      role: "user",
      content: "Draft a short launch update.",
      bin_ids_used: [],
      retrieval_mode: null,
      citations: [],
      provider_metadata: {},
      prompt_versions: {},
    },
    {
      id: "m-assistant-2",
      session_id: mockSessionB,
      user_id: "00000000-0000-0000-0000-000000000001",
      role: "assistant",
      content: "Launch update: rollout is on schedule, QA completed, and onboarding docs are ready.",
      bin_ids_used: [],
      retrieval_mode: "parametric",
      citations: [],
      provider_metadata: {},
      prompt_versions: { answer_generator_parametric: "v1" },
    },
  ],
};

function toSessionListItem(session: SessionRecord, messages: MessageRecord[]): SessionListItem {
  const sessionMessages = messages.filter((item) => item.session_id === session.id);
  const last = sessionMessages.length ? sessionMessages[sessionMessages.length - 1] : undefined;
  return {
    id: session.id,
    title: session.title,
    lastMessageAt: last ? new Date().toISOString() : null,
  };
}

function normalizeCitations(value: MessageRecord["citations"]): Citation[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is Citation => typeof item === "object" && item !== null && "chunk_id" in item);
}

function normalizeThinkingSteps(value: unknown): ThinkingStep[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter(
    (item): item is ThinkingStep =>
      typeof item === "object" &&
      item !== null &&
      "step_id" in item &&
      "label" in item &&
      "status" in item &&
      "detail" in item,
  );
}

function slugifyNamespacePart(value: string): string {
  const normalized = value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  return normalized || "bin";
}

function createVectorNamespace(title: string): string {
  return `bin-${slugifyNamespacePart(title)}-${Date.now().toString(36)}`;
}

export function useSessions() {
  if (mockMode) {
    return useQuery({
      queryKey: ["sessions"],
      queryFn: async () => mockSessions,
    });
  }

  return useQuery({
    queryKey: ["sessions"],
    queryFn: () => apiFetch<SessionRecord[]>("/api/v1/sessions"),
  });
}

export function useSession(sessionId: string | undefined) {
  if (mockMode) {
    return useQuery({
      queryKey: ["session", sessionId],
      enabled: Boolean(sessionId),
      queryFn: async () => mockSessions.find((session) => session.id === sessionId) as SessionRecord,
    });
  }

  return useQuery({
    queryKey: ["session", sessionId],
    enabled: Boolean(sessionId),
    queryFn: () => apiFetch<SessionRecord>(`/api/v1/sessions/${sessionId}`),
  });
}

export function useSessionMessages(sessionId: string | undefined) {
  if (mockMode) {
    return useQuery({
      queryKey: ["messages", sessionId],
      enabled: Boolean(sessionId),
      queryFn: async () => (sessionId ? (mockMessagesBySession[sessionId] ?? []) : []),
    });
  }

  return useQuery({
    queryKey: ["messages", sessionId],
    enabled: Boolean(sessionId),
    queryFn: () => apiFetch<MessageRecord[]>(`/api/v1/sessions/${sessionId}/messages`),
  });
}

export function useBins() {
  if (mockMode) {
    return useQuery({
      queryKey: ["bins"],
      queryFn: async () => mockBins,
    });
  }

  return useQuery({
    queryKey: ["bins"],
    queryFn: () => apiFetch<BinRecord[]>("/api/v1/bins"),
  });
}

export function useCreateBin() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: { title: string; description?: string | null }) =>
      apiFetch<BinRecord>("/api/v1/bins", {
        method: "POST",
        body: {
          title: payload.title,
          description: payload.description || null,
          vector_namespace: createVectorNamespace(payload.title),
        },
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["bins"] });
    },
  });
}

export function useUploadBinFile() {
  return useMutation({
    mutationFn: async ({ binId, file }: { binId: string; file: File }) => {
      const formData = new FormData();
      formData.append("file", file);
      return apiUpload<IngestionAcceptedResponse>(`/api/v1/bins/${binId}/items/upload`, formData);
    },
  });
}

export function useCreateSession() {
  const queryClient = useQueryClient();

  if (mockMode) {
    return useMutation({
      mutationFn: async (payload: { title: string; initial_bin_ids: string[]; last_active_bin_ids: string[] }) => ({
        id: `mock-${Date.now()}`,
        user_id: "00000000-0000-0000-0000-000000000001",
        title: payload.title,
        initial_bin_ids: payload.initial_bin_ids,
        last_active_bin_ids: payload.last_active_bin_ids,
      }),
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      },
    });
  }

  return useMutation({
    mutationFn: (payload: { title: string; initial_bin_ids: string[]; last_active_bin_ids: string[] }) =>
      apiFetch<SessionRecord>("/api/v1/sessions", {
        method: "POST",
        body: payload,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}

export function useDeleteSession() {
  const queryClient = useQueryClient();

  if (mockMode) {
    return useMutation({
      mutationFn: async (_sessionId: string) => undefined,
      onSuccess: async (_data, sessionId) => {
        if (useChatStore.getState().activeSessionId === sessionId) {
          useChatStore.getState().setActiveSession(null);
        }
        await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      },
    });
  }

  return useMutation({
    mutationFn: (sessionId: string) =>
      apiFetch<void>(`/api/v1/sessions/${sessionId}`, {
        method: "DELETE",
      }),
    onSuccess: async (_data, sessionId) => {
      if (useChatStore.getState().activeSessionId === sessionId) {
        useChatStore.getState().setActiveSession(null);
      }
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      await queryClient.invalidateQueries({ queryKey: ["history-sessions-messages"] });
    },
  });
}

export function useUpdateSessionBins() {
  const queryClient = useQueryClient();

  if (mockMode) {
    return useMutation({
      mutationFn: async ({ sessionId, binIds }: UpdateSessionBinsArgs) => {
        const match = mockSessions.find((item) => item.id === sessionId);
        return {
          ...(match ?? mockSessions[0]),
          id: sessionId,
          last_active_bin_ids: binIds,
        } as SessionRecord;
      },
      onSuccess: async (_data, vars) => {
        await queryClient.invalidateQueries({ queryKey: ["session", vars.sessionId] });
      },
    });
  }

  return useMutation({
    mutationFn: ({ sessionId, binIds }: UpdateSessionBinsArgs) =>
      apiFetch<SessionRecord>(`/api/v1/sessions/${sessionId}/bins`, {
        method: "PATCH",
        body: { bin_ids: binIds },
      }),
    onSuccess: async (_data, vars) => {
      await queryClient.invalidateQueries({ queryKey: ["session", vars.sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}

export function useChatStream() {
  const queryClient = useQueryClient();
  const { connect, cancel } = useSSE();
  const setStatus = useChatStore((state) => state.setStatus);
  const upsertThinkingStep = useChatStore((state) => state.upsertThinkingStep);
  const appendDraft = useChatStore((state) => state.appendDraft);
  const setCitations = useChatStore((state) => state.setCitations);
  const markDone = useChatStore((state) => state.markDone);
  const markError = useChatStore((state) => state.markError);
  const incrementRetry = useChatStore((state) => state.incrementRetry);

  const applyDone = useCallback(
    async (payload: StreamDonePayload, _cursor: string) => {
      const key = ["messages", payload.session_id];
      queryClient.setQueryData<MessageRecord[]>(key, (prev = []) => {
        const filtered = prev.filter((item) => item.id !== payload.message_id);
        return [
          ...filtered,
          {
            id: payload.message_id,
            session_id: payload.session_id,
            user_id: "",
            role: "assistant",
            content: payload.content,
            bin_ids_used: payload.bin_ids_used,
            retrieval_mode: payload.retrieval_mode,
            citations: payload.citations,
            provider_metadata: payload.provider_metadata,
            prompt_versions: payload.prompt_versions,
          },
        ];
      });
      await queryClient.invalidateQueries({ queryKey: key });
    },
    [queryClient],
  );

  const sendMessage = useCallback(
    async ({ sessionId, message, binIds, cursor }: SendMessageArgs) => {
      if (mockMode) {
        const key = ["messages", sessionId];
        queryClient.setQueryData<MessageRecord[]>(key, (prev = []) => [
          ...prev,
          {
            id: `mock-user-${Date.now()}`,
            session_id: sessionId,
            user_id: "00000000-0000-0000-0000-000000000001",
            role: "user",
            content: message,
            bin_ids_used: binIds,
            retrieval_mode: null,
            citations: [],
            provider_metadata: {},
            prompt_versions: {},
          },
        ]);

        setStatus("streaming");
        const mockThinking: ThinkingStep[] = [
          {
            step_id: "retrieval_decision:1",
            node_name: "retrieval_decision",
            label: "Selecting retrieval mode",
            status: "completed",
            detail: "Selected grounded retrieval.",
            attempt: 1,
          },
          {
            step_id: "answer_generator:1",
            node_name: "answer_generator",
            label: "Drafting answer",
            status: "completed",
            detail: "Drafted a grounded answer.",
            attempt: 1,
          },
        ];
        for (const step of mockThinking) {
          upsertThinkingStep(step, `mock:${Date.now()}`);
        }
        const chunks = ["Sure, ", "here is ", "a mock ", "streamed ", "answer."];
        for (const chunk of chunks) {
          await new Promise((resolve) => setTimeout(resolve, 60));
          appendDraft(chunk, `mock:${Date.now()}`);
        }
        const payload: StreamDonePayload = {
          message_id: `mock-assistant-${Date.now()}`,
          session_id: sessionId,
          content: "Sure, here is a mock streamed answer.",
          retrieval_mode: binIds.length ? "grounded" : "parametric",
          citations: binIds.length
            ? [
                {
                  item_name: "mock_source.txt",
                  chunk_excerpt: "Mock citation excerpt for screenshot mode.",
                  bin_title: "Policy Bin",
                  chunk_id: "mock_chunk_1",
                  score: 0.9,
                },
              ]
            : [],
          bin_ids_used: binIds,
          provider_metadata: { mocked: true, thinking_steps: mockThinking },
          prompt_versions: { answer_generator: "v1" },
        };
        await applyDone(payload, `mock:${Date.now()}`);
        markDone(payload, `mock:${Date.now()}`);
        return;
      }

      const key = ["messages", sessionId];
      setStatus(cursor ? "reconnecting" : "connecting");

      if (!cursor) {
        useChatStore.setState((state) => ({
          ...state,
          cursor: null,
          draftAssistantText: "",
          citations: [],
          thinkingSteps: [],
          retryCount: 0,
          lastError: null,
        }));
        queryClient.setQueryData<MessageRecord[]>(key, (prev = []) => {
          const optimisticUser: MessageRecord = {
            id: `optimistic-user-${Date.now()}`,
            session_id: sessionId,
            user_id: "",
            role: "user",
            content: message,
            bin_ids_used: binIds,
            retrieval_mode: null,
            citations: [],
            provider_metadata: {},
            prompt_versions: {},
          };
          return [...prev, optimisticUser];
        });
      }

      const attemptConnect = async (attempt: number, activeCursor: string | null): Promise<void> => {
        let streamError: StreamErrorPayload | null = null;

        try {
          await connect(
            `/api/v1/chat/sessions/${sessionId}/message`,
            {
              message: activeCursor ? undefined : message,
              bin_ids: activeCursor ? undefined : binIds,
              cursor: activeCursor ?? undefined,
            },
            {
              onThinking: (payload, eventCursor) => {
                upsertThinkingStep(payload, eventCursor);
              },
              onToken: (payload, eventCursor) => {
                appendDraft(payload.text, eventCursor);
              },
              onCitations: (payload, eventCursor) => {
                setCitations(payload.citations, eventCursor);
              },
              onDone: async (payload, eventCursor) => {
                await applyDone(payload, eventCursor);
                markDone(payload, eventCursor);
                await queryClient.invalidateQueries({ queryKey: ["sessions"] });
                await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
              },
              onHeartbeat: () => {
                setStatus("streaming");
              },
              onError: (payload) => {
                streamError = payload;
                markError(payload);
              },
              onUnknown: () => {
                // Non-fatal diagnostics only.
              },
            },
            { cursor: activeCursor },
          );
        } catch (error) {
          streamError = toStreamError(error);
          markError(streamError);
        }

        if (!streamError) {
          return;
        }
        if (streamError.code === "cursor_invalid") {
          return;
        }
        if (attempt >= RETRY_MAX) {
          return;
        }

        const resumeCursor = useChatStore.getState().cursor;
        if (!resumeCursor) {
          return;
        }

        incrementRetry();
        await new Promise((resolve) => setTimeout(resolve, RETRY_BASE_MS * (attempt + 1)));
        await attemptConnect(attempt + 1, resumeCursor);
      };

      await attemptConnect(0, cursor ?? null);
    },
    [appendDraft, applyDone, connect, incrementRetry, markDone, markError, queryClient, setCitations, setStatus, upsertThinkingStep],
  );

  const retryFromCursor = useCallback(
    async (sessionId: string) => {
      const cursor = useChatStore.getState().cursor;
      if (!cursor) {
        return;
      }
      await sendMessage({
        sessionId,
        message: "",
        binIds: [],
        cursor,
      });
    },
    [sendMessage],
  );

  const cancelStream = useCallback(() => {
    cancel();
    useChatStore.setState((state) => ({
      ...state,
      status: "offline",
      draftAssistantText: "",
      citations: [],
      thinkingSteps: [],
      retryCount: 0,
      lastError: null,
    }));
  }, [cancel]);

  return {
    sendMessage,
    retryFromCursor,
    cancelStream,
  };
}

export function useHistoryList() {
  if (mockMode) {
    const items = mockSessions.map((session) =>
      toSessionListItem(session, mockMessagesBySession[session.id] ?? []),
    );
    return {
      isLoading: false,
      error: null,
      items,
    };
  }

  const sessionsQuery = useSessions();
  const sessions = sessionsQuery.data ?? [];

  const messageQueries = useQueries({
    queries: sessions.map((session) => ({
      queryKey: ["messages", session.id],
      queryFn: () => apiFetch<MessageRecord[]>(`/api/v1/sessions/${session.id}/messages`),
      staleTime: 10000,
    })),
  });

  const isLoading = sessionsQuery.isLoading || messageQueries.some((query) => query.isLoading);
  const error = sessionsQuery.error ?? messageQueries.find((query) => query.error)?.error ?? null;

  const items = useMemo(() => {
    if (!sessions.length) {
      return [];
    }
    return sessions.map((session, index) => {
      const messages = (messageQueries[index]?.data ?? []) as MessageRecord[];
      return toSessionListItem(session, messages);
    });
  }, [messageQueries, sessions]);

  return {
    isLoading,
    error,
    items,
  };
}

export function useMessageViewModel(messages: MessageRecord[] | undefined) {
  return useMemo(() => {
    return (messages ?? []).map((message) => ({
      ...message,
      citationsNormalized: normalizeCitations(message.citations),
      thinkingSteps: normalizeThinkingSteps((message.provider_metadata as { thinking_steps?: unknown } | null)?.thinking_steps),
    }));
  }, [messages]);
}

export function toStreamError(error: unknown): StreamErrorPayload {
  if (error instanceof Error) {
    return { code: "client_error", message: error.message };
  }
  return { code: "client_error", message: "Unknown client error" };
}
