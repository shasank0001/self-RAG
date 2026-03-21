import { getToken } from "@/lib/auth/token";

export type ApiClientOptions = {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
};

export type StreamPostOptions = {
  signal?: AbortSignal;
  headers?: Record<string, string>;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export async function apiFetch<T>(path: string, options: ApiClientOptions = {}): Promise<T> {
  const { method = "GET", body, headers = {}, signal } = options;
  const token = await getToken();

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Request failed: ${response.status} ${errorText || response.statusText}`);
  }

  return (await response.json()) as T;
}

export async function streamPost(
  path: string,
  payload: unknown,
  options: StreamPostOptions = {},
): Promise<ReadableStream<Uint8Array> | null> {
  const { signal, headers = {} } = options;
  const token = await getToken();

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Stream request failed: ${response.status} ${errorText || response.statusText}`);
  }

  return response.body;
}
