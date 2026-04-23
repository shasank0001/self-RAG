import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { BinPicker } from "@/components/BinPicker";
import { CitationPanel } from "@/components/CitationPanel";
import { MessageContent } from "@/components/MessageContent";
import { RetrievalModeBadge } from "@/components/RetrievalModeBadge";
import { ThinkingPanel } from "@/components/ThinkingPanel";
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
  const streamThinkingSteps = useChatStore((state) => state.thinkingSteps);
  const lastError = useChatStore((state) => state.lastError);
  const resumeCursor = useChatStore((state) => state.cursor);
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
  }, [draftAssistantText, messages.length, streamThinkingSteps.length]);

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

  const errorReason = useMemo(() => {
    const reason = lastError?.details?.reason;
    if (typeof reason !== "string") {
      return null;
    }
    const trimmed = reason.trim();
    if (!trimmed || trimmed === lastError?.message) {
      return null;
    }
    return trimmed;
  }, [lastError]);

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
        <section className="chat-sidebar-section">
          <p className="chat-sidebar-label">Session status</p>
          <p className="connection-banner">{connectionLabel}</p>
        </section>
        {lastError ? (
          <div className="stream-error">
            <strong>{lastError.code}</strong>
            <p>{lastError.message}</p>
            {errorReason ? <p className="stream-error-reason">{errorReason}</p> : null}
            {sessionId && resumeCursor ? (
              <button type="button" onClick={() => void retryFromCursor(sessionId)}>
                Retry from cursor
              </button>
            ) : null}
          </div>
        ) : null}
        <section className="chat-sidebar-section">
          <div className="chat-sidebar-section-heading">
            <div>
              <p className="chat-sidebar-label">Knowledge context</p>
              <h3>Active bins</h3>
            </div>
            <Link to="/history" className="history-link">
              History
            </Link>
          </div>
          <BinPicker
            bins={(binsQuery.data ?? []).map((bin) => ({ id: bin.id, title: bin.title, description: bin.description }))}
            selectedBinIds={selectedBinIds}
            onChange={(next) => {
              void onSelectBins(next);
            }}
            disabled={status === "connecting" || status === "streaming" || status === "reconnecting"}
          />
        </section>
      </aside>

      <main className="chat-main">
        {sessionQuery.data?.title ? (
          <header className="chat-thread-header">
            <p className="chat-thread-kicker">Conversation</p>
            <h3>{sessionQuery.data.title}</h3>
          </header>
        ) : null}
        <div ref={messageListRef} className="message-list" onScroll={onTranscriptScroll}>
          {messages.length === 0 && !draftAssistantText && streamThinkingSteps.length === 0 ? (
            <section className="chat-empty-state">
              <p className="chat-empty-kicker">Ready when you are</p>
              <h4>Start with a direct question.</h4>
              <p>Use selected bins for grounded answers, or leave them empty for parametric mode.</p>
            </section>
          ) : null}
          {messages.map((message) => (
            <article
              key={message.id}
              className={message.role === "assistant" ? "message-row is-assistant" : "message-row is-user"}
            >
              <header className="message-meta">
                <strong>{message.role === "assistant" ? "Assistant" : "You"}</strong>
                {message.role === "assistant" ? <RetrievalModeBadge mode={message.retrieval_mode} /> : null}
              </header>
              {message.role === "assistant" ? <MessageContent content={message.content} /> : <p>{message.content}</p>}
              {message.role === "assistant" ? <ThinkingPanel steps={message.thinkingSteps} /> : null}
              {message.role === "assistant" ? <CitationPanel citations={message.citationsNormalized} /> : null}
            </article>
          ))}

          {draftAssistantText || streamThinkingSteps.length > 0 ? (
            <article className="message-row is-assistant is-draft">
              <header className="message-meta">
                <strong>Assistant</strong>
                <RetrievalModeBadge mode={streamCitations.length ? "grounded" : "parametric"} />
              </header>
              <ThinkingPanel steps={streamThinkingSteps} isStreaming />
              {draftAssistantText ? <MessageContent content={draftAssistantText} /> : null}
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
          <div className="composer-meta">
            <small>{selectedBinIds.length > 0 ? `${selectedBinIds.length} bins selected` : "No bins selected"}</small>
            {activeSessionId ? <small>Saved to history</small> : <small>Starts a new session on send</small>}
          </div>
        </footer>
      </main>
    </section>
  );
}
