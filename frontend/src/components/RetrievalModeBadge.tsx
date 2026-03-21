import type { RetrievalMode } from "@/types/chat";

type RetrievalModeBadgeProps = {
  mode: RetrievalMode | null;
};

export function RetrievalModeBadge({ mode }: RetrievalModeBadgeProps) {
  const resolved = mode ?? "parametric";
  return (
    <span className={resolved === "grounded" ? "retrieval-badge is-grounded" : "retrieval-badge is-parametric"}>
      {resolved === "grounded" ? "Grounded" : "Parametric"}
    </span>
  );
}
