import { Link } from "react-router-dom";

import { useBins } from "@/hooks/useChat";

export function BinListPage() {
  const binsQuery = useBins();

  return (
    <section className="bins-page page-card">
      <header className="bins-header">
        <h2>Bins</h2>
        <Link to="/chat" className="bins-cta">
          Start chat
        </Link>
      </header>
      <p className="bins-subtitle">Select bins in chat to switch between grounded and parametric responses.</p>
      <div className="bins-grid">
        {(binsQuery.data ?? []).map((bin) => (
          <article key={bin.id} className="bin-card">
            <h3>{bin.title}</h3>
            <p>{bin.description || "No description"}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
