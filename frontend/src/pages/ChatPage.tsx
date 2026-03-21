import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { BinPicker } from "@/components/BinPicker";
import { CitationPanel } from "@/components/CitationPanel";
import { RetrievalModeBadge } from "@/components/RetrievalModeBadge";
import {
  toStreamError,
  useBins,
  useChatStream,
  useCreateSession,
  useMessageViewModel,
  useSession,
  useSessionMessages,
  useUpdateSessionBins,
} from "@/hooks/useChat";
import { useChatStore } from "@/store/chatStore";

export function ChatPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const [draft, setDraft] = useState("");
  const [selectedBinIds, setSelectedBinIds] = useState<string[]>([]);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);

  const binsQuery = useBins();
  const sessionQuery = useSession(sessionId);
  const messagesQuery = useSessionMessages(sessionId);
  const createSession = useCreateSession();
  const updateSessionBins = useUpdateSessionBins();
  const { sendMessage, cancelStream, retryFromCursor } = useChatStream();
  const messages = useMessageViewModel(messagesQuery.data);

  const status = useChatStore((state) => state.status);
  const draftAssistantText = useChatStore((state) => state.draftAssistantText);
  const streamCitations = useChatStore((state) => state.citations);
  const lastError = useChatStore((state) => state.lastError);
  const activeSessionId = useChatStore((state) => state.activeSessionId);
  const setActiveSession = useChatStore((state) => state.setActiveSession);
  const resetStreamState = useChatStore((state) => state.resetStreamState);
  const isExplicitNewChat = new URLSearchParams(location.search).get("new") === "1";

  useEffect(() => {
    if (sessionId || isExplicitNewChat || !activeSessionId) {
      return;
    }

    navigate(`/chat/${activeSessionId}`, { replace: true });
  }, [activeSessionId, isExplicitNewChat, navigate, sessionId]);

  useEffect(() => {
    setActiveSession(sessionId ?? null);
    resetStreamState();
  }, [resetStreamState, sessionId, setActiveSession]);

  useEffect(() => {
    return () => {
      cancelStream();
      resetStreamState();
    };
  }, [cancelStream, resetStreamState]);

  useEffect(() => {
    if (!sessionQuery.data) {
      return;
    }
    setSelectedBinIds(sessionQuery.data.last_active_bin_ids);
  }, [sessionQuery.data]);

  useEffect(() => {
    const messageList = messageListRef.current;
    if (!messageList) {
      return;
    }

    shouldStickToBottomRef.current = true;
    messageList.scrollTo({ top: messageList.scrollHeight });
  }, [sessionId]);

  useEffect(() => {
    const messageList = messageListRef.current;
    if (!messageList || !shouldStickToBottomRef.current) {
      return;
    }

    messageList.scrollTo({ top: messageList.scrollHeight, behavior: "smooth" });
  }, [draftAssistantText, messages.length]);

  const onSelectBins = async (next: string[]) => {
    setSelectedBinIds(next);
    if (!sessionId) {
      return;
    }
    await updateSessionBins.mutateAsync({ sessionId, binIds: next });
  };

  const onSend = async () => {
    const trimmed = draft.trim();
    if (!trimmed) {
      return;
    }

    let currentSessionId = sessionId;
    try {
      if (!currentSessionId) {
        const created = await createSession.mutateAsync({
          title: trimmed.slice(0, 80),
          initial_bin_ids: selectedBinIds,
          last_active_bin_ids: selectedBinIds,
        });
        currentSessionId = created.id;
        navigate(`/chat/${currentSessionId}`, { replace: true });
      }

      if (!currentSessionId) {
        return;
      }

      setDraft("");
      await sendMessage({
        sessionId: currentSessionId,
        message: trimmed,
        binIds: selectedBinIds,
      });
    } catch (error) {
      const payload = toStreamError(error);
      useChatStore.getState().markError(payload);
    }
  };

  const connectionLabel = useMemo(() => {
    if (status === "reconnecting") {
      return "Reconnecting stream...";
    }
    if (status === "connecting") {
      return "Connecting stream...";
    }
    if (status === "offline") {
      return "Stream offline";
    }
    if (status === "error") {
      return "Stream error";
    }
    if (status === "streaming") {
      return "Streaming";
    }
    return "Idle";
  }, [status]);

  const onTranscriptScroll = () => {
    const messageList = messageListRef.current;
    if (!messageList) {
      return;
    }

    const distanceFromBottom = messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight;
    shouldStickToBottomRef.current = distanceFromBottom < 120;
  };

  return (
    <section className="chat-layout page-card">
      <aside className="chat-sidebar">
        <div className="chat-sidebar-header">
          <h2>{sessionId ? "Session" : "New chat"}</h2>
          <Link to="/chat?new=1" className="new-chat-link">
            New chat
          </Link>
        </div>
        <p className="connection-banner">{connectionLabel}</p>
        {lastError ? (
          <div className="stream-error">
            <strong>{lastError.code}</strong>
            <p>{lastError.message}</p>
            {sessionId ? (
              <button type="button" onClick={() => void retryFromCursor(sessionId)}>
                Retry from cursor
              </button>
            ) : null}
          </div>
        ) : null}
        <BinPicker
          bins={(binsQuery.data ?? []).map((bin) => ({ id: bin.id, title: bin.title, description: bin.description }))}
          selectedBinIds={selectedBinIds}
          onChange={(next) => {
            void onSelectBins(next);
          }}
          disabled={status === "connecting" || status === "streaming" || status === "reconnecting"}
        />
        <Link to="/history" className="history-link">
          Open history
        </Link>
      </aside>

      <main className="chat-main">
        <div ref={messageListRef} className="message-list" onScroll={onTranscriptScroll}>
          {messages.map((message) => (
            <article
              key={message.id}
              className={message.role === "assistant" ? "message-row is-assistant" : "message-row is-user"}
            >
              <header className="message-meta">
                <strong>{message.role === "assistant" ? "Assistant" : "You"}</strong>
                {message.role === "assistant" ? <RetrievalModeBadge mode={message.retrieval_mode} /> : null}
              </header>
              <p>{message.content}</p>
              {message.role === "assistant" ? <CitationPanel citations={message.citationsNormalized} /> : null}
            </article>
          ))}

          {draftAssistantText ? (
            <article className="message-row is-assistant is-draft">
              <header className="message-meta">
                <strong>Assistant</strong>
                <RetrievalModeBadge mode={streamCitations.length ? "grounded" : "parametric"} />
              </header>
              <p>{draftAssistantText}</p>
              <CitationPanel citations={streamCitations} />
            </article>
          ) : null}
        </div>

        <footer className="composer">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask a question about your bins..."
            rows={4}
            disabled={status === "connecting" || status === "streaming" || status === "reconnecting"}
          />
          <div className="composer-actions">
            <button type="button" onClick={onSend} disabled={!draft.trim()}>
              Send
            </button>
            <button type="button" onClick={cancelStream} disabled={status !== "streaming" && status !== "connecting"}>
              Cancel
            </button>
          </div>
          {activeSessionId ? <small>Active session: {activeSessionId}</small> : null}
        </footer>
      </main>
    </section>
  );
}
