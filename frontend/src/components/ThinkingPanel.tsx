import { useEffect, useState } from "react";

import type { ThinkingStep } from "@/types/chat";

type ThinkingPanelProps = {
  steps: ThinkingStep[];
  isStreaming?: boolean;
};

function formatStatus(status: ThinkingStep["status"]) {
  if (status === "completed") {
    return "done";
  }
  if (status === "failed") {
    return "failed";
  }
  if (status === "skipped") {
    return "skipped";
  }
  return "live";
}

export function ThinkingPanel({ steps, isStreaming = false }: ThinkingPanelProps) {
  const [open, setOpen] = useState(isStreaming);

  useEffect(() => {
    if (isStreaming) {
      setOpen(true);
    }
  }, [isStreaming]);

  if (!steps.length) {
    return null;
  }

  return (
    <section className="thinking-panel">
      <button
        type="button"
        className={open ? "thinking-toggle is-open" : "thinking-toggle"}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span>Thinking</span>
        <small>{isStreaming ? "live" : `${steps.length} step${steps.length === 1 ? "" : "s"}`}</small>
      </button>
      {open ? (
        <div className="thinking-list">
          {steps.map((step) => (
            <article key={step.step_id} className={`thinking-item is-${step.status}`}>
              <header>
                <strong>{step.label}</strong>
                <span>{formatStatus(step.status)}</span>
              </header>
              <p>{step.detail}</p>
              {step.provider || step.model ? (
                <small>
                  {[step.provider, step.model].filter(Boolean).join(" / ")}
                </small>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
