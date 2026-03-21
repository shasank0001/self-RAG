import type { Citation } from "@/types/chat";

type CitationPanelProps = {
  citations: Citation[];
};

export function CitationPanel({ citations }: CitationPanelProps) {
  if (!citations.length) {
    return null;
  }

  return (
    <section className="citation-panel">
      <h4>Sources</h4>
      <div className="citation-list">
        {citations.map((citation) => (
          <article key={`${citation.chunk_id}-${citation.item_name}`} className="citation-item">
            <p className="citation-meta">
              {citation.bin_title} · {citation.item_name}
            </p>
            <p className="citation-excerpt">{citation.chunk_excerpt}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
