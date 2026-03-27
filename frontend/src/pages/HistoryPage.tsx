import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { CitationPanel } from "@/components/CitationPanel";
import { MessageContent } from "@/components/MessageContent";
import { RetrievalModeBadge } from "@/components/RetrievalModeBadge";
import { ThinkingPanel } from "@/components/ThinkingPanel";
import { useDeleteSession, useHistoryList, useMessageViewModel, useSessionMessages } from "@/hooks/useChat";

export function HistoryPage() {
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const history = useHistoryList();
  const detail = useSessionMessages(sessionId);
  const messages = useMessageViewModel(detail.data);
  const deleteSession = useDeleteSession();

  const selected = useMemo(() => history.items.find((item) => item.id === sessionId), [history.items, sessionId]);

  const onDelete = async (id: string) => {
    await deleteSession.mutateAsync(id);
    if (sessionId === id) {
      navigate("/history", { replace: true });
    }
  };

  return (
    <section className="history-layout page-card">
      <aside className="history-sidebar">
        <h2>History</h2>
        <p className="history-note">Read-only transcript view.</p>
        <div className="history-list">
          {history.items.map((item) => (
            <article key={item.id} className={sessionId === item.id ? "history-item is-active" : "history-item"}>
              <Link to={`/history/${item.id}`}>
                <strong>{item.title}</strong>
                <small>{item.lastMessageAt ? new Date(item.lastMessageAt).toLocaleString() : "No messages yet"}</small>
              </Link>
              <button type="button" onClick={() => void onDelete(item.id)} disabled={deleteSession.isPending}>
                Delete
              </button>
            </article>
          ))}
        </div>
      </aside>

      <main className="history-detail">
        {sessionId ? (
          <>
            <header className="history-detail-header">
              <h3>{selected?.title ?? "Session"}</h3>
              <Link className="history-continue" to={`/chat/${sessionId}`}>
                Continue session
              </Link>
            </header>
            <div className="message-list is-readonly">
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
            </div>
            <div className="readonly-composer">
              <textarea disabled value="History is read-only. Use Continue session to keep chatting." />
            </div>
          </>
        ) : (
          <div className="history-empty">Select a session from the left to view its read-only transcript.</div>
        )}
      </main>
    </section>
  );
}
