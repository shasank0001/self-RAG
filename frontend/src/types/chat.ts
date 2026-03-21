export type RetrievalMode = "grounded" | "parametric";

export type BinRecord = {
  id: string;
  user_id: string;
  title: string;
  description?: string | null;
  vector_namespace: string;
};

export type IngestionStatus = "queued" | "running" | "succeeded" | "failed";

export type IngestionAcceptedResponse = {
  job_id: string;
  item_id: string;
  bin_id: string;
  status: IngestionStatus;
  attempt_count: number;
  max_attempts: number;
};

export type IngestionStatusResponse = {
  job_id: string;
  item_id: string | null;
  bin_id: string;
  status: IngestionStatus;
  content_hash: string | null;
  attempt_count: number;
  max_attempts: number;
  last_error: string | null;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
};

export type Citation = {
  item_name: string;
  chunk_excerpt: string;
  bin_title: string;
  chunk_id: string;
  score?: number | null;
};

export type SessionRecord = {
  id: string;
  user_id: string;
  title: string;
  initial_bin_ids: string[];
  last_active_bin_ids: string[];
};

export type MessageRecord = {
  id: string;
  session_id: string;
  user_id: string;
  role: "user" | "assistant";
  content: string;
  bin_ids_used: string[];
  retrieval_mode: RetrievalMode | null;
  citations: Citation[] | Record<string, unknown>;
  provider_metadata: Record<string, unknown>;
  prompt_versions: Record<string, string>;
};

export type StreamDonePayload = {
  message_id: string;
  session_id: string;
  content: string;
  retrieval_mode: RetrievalMode;
  citations: Citation[];
  bin_ids_used: string[];
  provider_metadata: Record<string, unknown>;
  prompt_versions: Record<string, string>;
};

export type StreamErrorPayload = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export type StreamEnvelope<T = Record<string, unknown>> = {
  id: string;
  event: string;
  data: T;
  ts: string;
};

export type StreamEventName = "token" | "citations" | "done" | "heartbeat" | "error";

export type SessionListItem = {
  id: string;
  title: string;
  lastMessageAt: string | null;
};
