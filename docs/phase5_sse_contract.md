# Phase 5 SSE Contract

This document defines the streaming protocol for chat turns over authenticated `POST` + `fetch` streaming.

## Endpoint

- `POST /api/v1/chat/sessions/{session_id}/message`
- Content type: `application/json`
- Response content type: `text/event-stream`

## Request Body

```json
{
  "message": "string | optional when cursor is provided",
  "bin_ids": ["uuid", "..."],
  "cursor": "assistant_message_id:sequence_number",
  "research": "boolean | default false, deep-research mode"
}
```

Rules:

- New stream: send `message` and optional `bin_ids`.
- Deep research: send `research: true` with `message` and at least one selected bin. The graph plans up to 3 sub-questions (bounded by `pipeline.research` config), retrieves per sub-question with one rewrite hop each, then synthesizes a structured report with per-claim citations. Progress streams as `thinking` events (`research_planner`, `research_executor`, `research_synthesizer`).
- Resume stream: send `cursor` (or `Last-Event-ID` header).
- If no `message` is provided and no valid `cursor` exists, server emits `error` event.

## Event Envelope

Each SSE frame uses standard SSE fields and a JSON envelope:

```text
event: <event_name>
id: <assistant_message_id:sequence_number>
data: {"id":"...","event":"...","data":{...},"ts":"..."}

```

Envelope fields:

- `id`: monotonic cursor for reconnect
- `event`: event name
- `data`: event payload
- `ts`: ISO-8601 UTC timestamp

## Event Types

- `thinking`
  - Payload:
    - `step_id`
    - `node_name`
    - `label`
    - `status` (`started|completed|failed|skipped`)
    - `detail`
    - `attempt`
    - optional `provider`
    - optional `model`
  - Emitted while the graph is running, before answer text tokens begin.
- `token`
  - Payload: `{ "text": "chunk" }`
  - Emitted as small text chunks.
- `heartbeat`
  - Payload: `{ "status": "alive" }`
  - Emitted periodically during long streams.
- `citations`
  - Payload: `{ "citations": [...], "retrieval_mode": "grounded|parametric" }`
  - Emitted after token chunks.
- `done`
  - Payload: finalized assistant payload:
    - `message_id`
    - `session_id`
    - `content`
    - `retrieval_mode`
    - `citations`
    - `bin_ids_used`
    - `provider_metadata`
    - `prompt_versions`
- `error`
  - Payload: `{ "code": "...", "message": "...", "details": {...} }`
  - Terminates stream semantics for the current request.

## Cursor and Resume Semantics

- Cursor format: `<assistant_message_id>:<sequence_number>`.
- Sequence numbers are monotonic and increase by one per emitted event.
- Stream metadata and replay events are persisted in `chat_messages.provider_metadata.stream`.
- On resume:
  - Valid cursor: replay begins at next event.
  - Stale/invalid cursor: emit `error` with `code=cursor_invalid` and stop.

## Ordering and Terminal Semantics

Expected order for successful runs:

1. zero or more `thinking`
2. zero or more `token`
3. zero or more `heartbeat`
4. one `citations`
5. one `done`

`done` is emitted exactly once on success.

## Idempotency

- Reconnect does not create new assistant messages when a valid cursor is used.
- Replay source is persisted event history for the assistant turn.

## Happy Path Example

```text
event: thinking
id: 6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:1
data: {"id":"6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:1","event":"thinking","data":{"step_id":"retrieval_decision:1","node_name":"retrieval_decision","label":"Selecting retrieval mode","status":"completed","detail":"Selected grounded retrieval.","attempt":1},"ts":"2026-03-21T00:00:00+00:00"}

event: token
id: 6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:2
data: {"id":"6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:2","event":"token","data":{"text":"Grounded answer "},"ts":"2026-03-21T00:00:00+00:00"}

event: citations
id: 6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:3
data: {"id":"6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:3","event":"citations","data":{"citations":[{"item_name":"doc.txt","chunk_excerpt":"evidence","bin_title":"Knowledge","chunk_id":"chunk-1"}],"retrieval_mode":"grounded"},"ts":"2026-03-21T00:00:00+00:00"}

event: done
id: 6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:4
data: {"id":"6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:4","event":"done","data":{"message_id":"6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31","session_id":"57c4d0f7-38de-4aa6-8d5c-3f53a0afe4a2","content":"Grounded answer about policy obligations.","retrieval_mode":"grounded","citations":[{"item_name":"doc.txt","chunk_excerpt":"evidence","bin_title":"Knowledge","chunk_id":"chunk-1"}],"bin_ids_used":[],"provider_metadata":{"thinking_steps":[{"step_id":"retrieval_decision:1","node_name":"retrieval_decision","label":"Selecting retrieval mode","status":"completed","detail":"Selected grounded retrieval.","attempt":1,"ts":"2026-03-21T00:00:00+00:00"}]},"prompt_versions":{}},"ts":"2026-03-21T00:00:00+00:00"}

```

## Failure Path Examples

### Invalid Cursor

```text
event: error
id: 6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:9
data: {"id":"6e2b8f0f-6a8d-4c51-98ec-c2d84f10ed31:9","event":"error","data":{"code":"cursor_invalid","message":"Resume cursor is stale or invalid","details":{}},"ts":"2026-03-21T00:00:00+00:00"}

```

### Missing Message on New Request

```text
event: error
id: error:0
data: {"id":"error:0","event":"error","data":{"code":"message_required","message":"Message is required when cursor is not provided","details":{}},"ts":"2026-03-21T00:00:00+00:00"}

```
