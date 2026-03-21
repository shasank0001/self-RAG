# Self-RAG Knowledge Chat — Project Overview

> A full-stack AI chat application where users organise their personal knowledge into named **Bins**, select any combination of bins per session, and get answers grounded in their own documents via a Self-RAG pipeline, with automatic fallback to LLM parametric memory when retrieval is not used.

---

## Table of Contents

1. [Project Summary](#1-project-summary)
2. [Core Concepts](#2-core-concepts)
3. [Feature Requirements](#3-feature-requirements)
   - 3.1 [Authentication](#31-authentication)
   - 3.2 [Bins — Knowledge Management](#32-bins--knowledge-management)
   - 3.3 [Chat Interface](#33-chat-interface)
   - 3.4 [Chat History](#34-chat-history)
4. [System Architecture](#4-system-architecture)
5. [Tech Stack & Key Decisions](#5-tech-stack--key-decisions)
6. [Database Schema](#6-database-schema)
7. [API Design](#7-api-design)
   - 7.1 [Auth Routes](#71-auth-routes)
   - 7.2 [Bin Routes](#72-bin-routes)
   - 7.3 [Document Routes](#73-document-routes)
   - 7.4 [Chat Routes](#74-chat-routes)
   - 7.5 [History Routes](#75-history-routes)
8. [Self-RAG Pipeline](#8-self-rag-pipeline)
   - 8.1 [Pipeline Overview](#81-pipeline-overview)
   - 8.2 [Node Descriptions](#82-node-descriptions)
   - 8.3 [Multi-Bin Retrieval Strategy](#83-multi-bin-retrieval-strategy)
   - 8.4 [No-Bin Fallback Behaviour](#84-no-bin-fallback-behaviour)
9. [Embedding Layer](#9-embedding-layer)
10. [LLM Routing Layer](#10-llm-routing-layer)
11. [Ingestion Pipeline](#11-ingestion-pipeline)
12. [Frontend — Pages & Components](#12-frontend--pages--components)
13. [Project Structure](#13-project-structure)
14. [Environment Variables](#14-environment-variables)
15. [References & Docs](#15-references--docs)

---

## 1. Project Summary

Self-RAG Knowledge Chat is a personal AI assistant that lets users build and manage private knowledge bases called **Bins**. Each bin holds documents and text the user has uploaded or pasted. During a chat session, the user selects one or more bins; the Self-RAG pipeline retrieves relevant content from those bins, grades relevance, rewrites the query if needed, generates a grounded answer, and checks for hallucinations before returning a response.

If no bin is selected, the system falls back to the LLM's own parametric knowledge with a clear indicator shown in the UI. When bins are selected, the retrieval decision node may still skip retrieval for clearly non-document-dependent queries; these replies are also explicitly labelled as parametric. All sessions are persisted as chat history, allowing users to review and continue previous conversations.

### Goals

- Give users fine-grained control over what knowledge the AI draws from.
- Make knowledge management intuitive — creating, populating, and switching bins should feel as natural as managing folders.
- Keep the AI honest — every answer is either grounded in the user's documents or explicitly labelled as coming from the model's general knowledge.
- Build an architecture where the embedding model and LLM provider can be changed with a config edit, not a code change.

---

## 2. Core Concepts

### Bin

A named, private collection of documents and text snippets that belong to a single user. A bin has a title, an optional description, and one or more content items. Bins are stored in PostgreSQL (metadata) and Pinecone (vector embeddings).

### Content Item

A single piece of content inside a bin. Supported types at launch:

- **File upload** — PDF, `.txt`, `.docx`
- **Pasted text** — raw text input directly in the app

Each content item is chunked and embedded during ingestion. Uploaded file bytes are stored in object storage or local disk, with `file_path` metadata in PostgreSQL, while pasted text is stored directly in PostgreSQL for display and management.

### Chat Session

A conversation thread that is linked to a user and optionally to one or more bins. The selected bins determine which Pinecone namespaces are queried during retrieval. Sessions and all messages are persisted in PostgreSQL.

### Retrieval Mode

Determined per assistant response:

- **Grounded mode** — retrieval runs and the final answer is grounded in retrieved bin content.
- **Parametric mode** — no bins are selected, or the retrieval decision node returns `skip` for the current message; the answer comes from model parametric knowledge. The UI always displays this mode explicitly.

---

## 3. Feature Requirements

### 3.1 Authentication

- Authentication is handled entirely by **Clerk**. The backend validates Clerk-issued JWTs on every protected request.
- Users sign up and log in via Clerk's hosted UI (email/password + OAuth).
- All user data — bins, documents, sessions — is scoped to the Clerk `user_id`.
- Clerk user records are mirrored into a local `users` table via signed webhook sync, and owned resources reference this table for relational integrity.
- Webhook endpoint: `POST /webhooks/clerk`.
- Webhook verification: requests are validated with Svix headers (`svix-id`, `svix-timestamp`, `svix-signature`) using `CLERK_WEBHOOK_SECRET`.
- Subscribed events: `user.created`, `user.updated`, `user.deleted`.
- Event handling:
  - `user.created` upserts the local `users` row.
  - `user.updated` patches mirrored profile fields (for example, primary email).
  - `user.deleted` sets `users.is_deleted = true` (soft delete) so FK integrity remains intact for historical records.

### 3.2 Bins — Knowledge Management

#### Creating and editing bins

- Users can create a new bin with a required title and an optional description.
- Bin title and description can be edited after creation.
- Bins can be deleted. Deleting a bin deletes all its content items from PostgreSQL and all associated vectors from the bin's Pinecone namespace.

#### Adding content to a bin

Users can add content to a bin in two ways:

**File upload**

- Accepted formats: PDF, `.txt`, `.docx`
- Max file size: 20 MB per file
- Multiple files can be uploaded to a bin in one operation
- On upload, file bytes are stored in object storage/local disk and the storage path is saved in PostgreSQL; the content is then chunked, embedded, and upserted into Pinecone under the bin's namespace
- Upload status is shown per-file (queued → processing → ready / error)

**Paste text**

- Users paste or type raw text directly in the app
- The text is given a user-defined name/label for identification
- Stored in PostgreSQL as a content item and processed through the same chunking + embedding pipeline as files

#### Managing bin contents

- Users can view all content items inside a bin, showing name, type, size, and ingest status
- Individual content items can be deleted (removes from PostgreSQL and purges their vectors from Pinecone via a metadata filter delete on `item_id`)
- A bin-level stats view shows total items, total chunks, and storage used

#### Bin list view

- All bins are listed on a dedicated Bins page
- Each bin card shows title, description, item count, and last-updated date
- Bins can be sorted by name or last-updated

### 3.3 Chat Interface

#### Starting a session

- Users can start a new chat from the chat page
- Before or during the conversation, users select which bins to use via a multi-select bin picker in the chat sidebar
- Selected bins can be changed at any point during an ongoing session; the change takes effect from the next message onwards and is persisted so the session can be accurately resumed later
- If no bins are selected, the UI shows a "Parametric mode" indicator — the LLM answers without retrieval

#### Message behaviour

- User sends a message → the pipeline runs → streaming response is shown in the chat window
- Each assistant message is tagged with its retrieval mode: **Grounded** (bin names shown) or **Parametric** (no bins selected, or retrieval explicitly skipped)
- Source citations are shown under grounded answers — each citation shows the content item name and the relevant chunk excerpt

#### Bin picker

- A collapsible sidebar panel lists all the user's bins
- Bins can be toggled on/off with checkboxes
- Search/filter by bin name for users with many bins
- Active bin selection is shown as a pill summary above the chat input

### 3.4 Chat History

- All sessions are automatically saved
- The sidebar shows a chronological list of past sessions with the session title (auto-generated from the first message) and date
- Users can click a past session in `/history` to view the full conversation read-only; the bin picker is restored to the `last_active_bin_ids` state from that session
- Users can continue the same session in editable mode via `/chat/:sessionId` with the last active bins restored
- Users can delete a session (removes from PostgreSQL but does not affect bin contents)
- Sessions store: session ID, user ID, initial and last-active bin IDs, all messages with role, content, citations, retrieval mode, and timestamp

---

## 4. System Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                        React Frontend                              │
│   Auth (Clerk)  │  Bins Manager  │  Chat UI  │  History Viewer     │
└─────────────────────────┬──────────────────────────────────────────┘
                          │ HTTPS / REST + POST fetch streaming
┌─────────────────────────▼──────────────────────────────────────────┐
│                       FastAPI Backend                              │
│                                                                    │
│   Clerk JWT validation middleware                                  │
│   Bin & Document CRUD  │  Ingestion worker  │  Chat endpoint       │
│                        │                    │                      │
│               PostgreSQL                LangGraph                  │
│           (users, bins, docs,          Self-RAG Pipeline           │
│            sessions, messages)                │                    │
│                                         LLM Router                 │
│                                    (Cerebras / OpenRouter /        │
│                                     OpenAI / Ollama)               │
│                                               │                    │
│                                    Embedding Router                │
│                                 (Qwen3 0.6b → Gemini Emb 2)        │
│                                               │                    │
└───────────────────────────────────────────────┼────────────────────┘
                                                │
                                         Pinecone
                               (one namespace per bin)
```

### Key architectural decisions

**One Pinecone namespace per bin.** Each bin maps to a dedicated Pinecone namespace keyed by `bin_id`. This allows multi-bin retrieval to query multiple namespaces via parallel async calls — one per namespace — and merge results before grading. It also makes bin deletion clean — purge the entire namespace instantly.

**Ingestion is asynchronous.** File processing (parsing, chunking, embedding, upsert) is decoupled from the HTTP response. The upload endpoint returns immediately with a `processing` status; a background worker handles ingestion and updates the item's status in PostgreSQL when done.

**Streaming via POST fetch stream.** The chat message endpoint remains a protected `POST` route and streams an SSE-formatted event stream over `fetch` + `ReadableStream` parsing on the frontend. Citations and metadata are sent as final structured events after token streaming completes.

**Clerk handles all auth.** The backend never stores passwords or issues tokens. Every API request carries a Clerk JWT in the `Authorization` header; the FastAPI middleware validates it and extracts the `user_id` before the route handler runs.

---

## 5. Tech Stack & Key Decisions

### Frontend

| Concern     | Technology                                    | Decision rationale                                                      |
| ----------- | --------------------------------------------- | ----------------------------------------------------------------------- |
| Framework   | React (Vite)                                  | Fast build times, component ecosystem                                   |
| Styling     | Tailwind CSS                                  | Utility-first, rapid UI iteration                                       |
| Auth        | Clerk React SDK                               | Drop-in components, handles sessions                                    |
| HTTP client | TanStack Query                                | Caching, background refetch for ingest status                           |
| Streaming   | `fetch` + `ReadableStream` (SSE event format) | Supports `POST` + `Authorization` header while keeping SSE-style events |
| State       | Zustand                                       | Lightweight, minimal boilerplate                                        |

### Backend

| Concern          | Technology                                          | Decision rationale                                   |
| ---------------- | --------------------------------------------------- | ---------------------------------------------------- |
| Framework        | FastAPI                                             | Async-native, auto OpenAPI docs, Pydantic validation |
| Auth             | Clerk Python SDK                                    | JWT verification without a custom auth system        |
| ORM              | SQLAlchemy 2.0 (async)                              | Type-safe queries, alembic migrations                |
| Migrations       | Alembic                                             | Schema versioning                                    |
| Background tasks | FastAPI `BackgroundTasks` (MVP)                     | Simple async ingestion without a separate queue      |
| File parsing     | PyMuPDF (PDF), `python-docx` (docx), built-in (txt) | Reliable extraction, no external service             |

### Data & AI

| Concern                | Technology                                           | Decision rationale                                      |
| ---------------------- | ---------------------------------------------------- | ------------------------------------------------------- |
| Relational DB          | PostgreSQL                                           | Proven, rich JSON support, Alembic-compatible           |
| Vector store           | Pinecone Serverless                                  | Managed, namespace-based multi-tenancy, free tier       |
| Pipeline orchestration | LangGraph                                            | Stateful graph, conditional edges, built for RAG        |
| Embeddings — MVP       | Qwen3 Embedding 0.6b via Ollama                      | Local, zero-cost, 1024-dim, no API key required         |
| Embeddings — upgrade   | Gemini Embedding 2 (`gemini-embedding-exp-03-07`)    | 3072-dim matryoshka, state-of-the-art retrieval quality |
| LLM — graders          | Cerebras (`llama-3.3-70b`)                           | Ultra-fast inference for binary yes/no decisions        |
| LLM — generation       | OpenRouter (`meta-llama/llama-3.1-8b-instruct:free`) | Flexible model routing, cost control                    |
| LLM — local fallback   | Ollama (`llama3.2`)                                  | Offline capability, privacy-first option                |
| Auth provider          | Clerk                                                | Hosted auth, social logins, webhooks, free tier         |

---

## 6. Database Schema

All tables include `created_at` and `updated_at` timestamps. Clerk remains the source of authentication, and a local `users` table is synchronized from Clerk to provide relational integrity for `user_id` foreign keys.

### `users`

Mirrors Clerk users for ownership joins and foreign key constraints.

| Column       | Type         | Notes                                      |
| ------------ | ------------ | ------------------------------------------ |
| `id`         | VARCHAR PK   | Clerk user ID (for example, `user_abc123`) |
| `email`      | VARCHAR(320) | Optional denormalized profile field        |
| `is_deleted` | BOOLEAN      | Soft-delete marker for `user.deleted`      |
| `created_at` | TIMESTAMPTZ  |                                            |
| `updated_at` | TIMESTAMPTZ  |                                            |

### `bins`

Stores bin metadata.

| Column               | Type                  | Notes                                    |
| -------------------- | --------------------- | ---------------------------------------- |
| `id`                 | UUID PK               |                                          |
| `user_id`            | VARCHAR FK → users.id | Clerk user ID                            |
| `title`              | VARCHAR(255)          | Required                                 |
| `description`        | TEXT                  | Optional                                 |
| `pinecone_namespace` | VARCHAR               | Equal to `id`; used for vector isolation |
| `created_at`         | TIMESTAMPTZ           |                                          |
| `updated_at`         | TIMESTAMPTZ           |                                          |

### `content_items`

Stores individual pieces of content within a bin.

| Column            | Type                  | Notes                                    |
| ----------------- | --------------------- | ---------------------------------------- |
| `id`              | UUID PK               |                                          |
| `bin_id`          | UUID FK → bins        | Cascade delete                           |
| `user_id`         | VARCHAR FK → users.id | Denormalised for fast per-user queries   |
| `type`            | ENUM                  | `file` or `text`                         |
| `name`            | VARCHAR(255)          | User-defined label                       |
| `file_path`       | TEXT                  | Storage path (null for text items)       |
| `raw_text`        | TEXT                  | Stored text (null for file items)        |
| `file_size_bytes` | INTEGER               |                                          |
| `mime_type`       | VARCHAR               |                                          |
| `ingest_status`   | ENUM                  | `queued`, `processing`, `ready`, `error` |
| `ingest_error`    | TEXT                  | Error message if status is `error`       |
| `chunk_count`     | INTEGER               | Set after ingestion completes            |
| `created_at`      | TIMESTAMPTZ           |                                          |
| `updated_at`      | TIMESTAMPTZ           |                                          |

### `chat_sessions`

Stores chat session metadata.

| Column                | Type                  | Notes                                                       |
| --------------------- | --------------------- | ----------------------------------------------------------- |
| `id`                  | UUID PK               |                                                             |
| `user_id`             | VARCHAR FK → users.id | Clerk user ID                                               |
| `title`               | VARCHAR(255)          | Auto-generated from first user message                      |
| `initial_bin_ids`     | UUID[]                | Snapshot of bins selected when the session was created      |
| `last_active_bin_ids` | UUID[]                | Updated by PATCH endpoint; restored when session is resumed |
| `created_at`          | TIMESTAMPTZ           |                                                             |
| `updated_at`          | TIMESTAMPTZ           |                                                             |

### `messages`

Stores individual messages within a session.

| Column           | Type                    | Notes                                                                                   |
| ---------------- | ----------------------- | --------------------------------------------------------------------------------------- |
| `id`             | UUID PK                 |                                                                                         |
| `session_id`     | UUID FK → chat_sessions | Cascade delete                                                                          |
| `user_id`        | VARCHAR FK → users.id   | Denormalised                                                                            |
| `role`           | ENUM                    | `user` or `assistant`                                                                   |
| `content`        | TEXT                    | Message text                                                                            |
| `retrieval_mode` | ENUM                    | `grounded` or `parametric`; populated only for assistant messages                       |
| `citations`      | JSONB                   | Array of `{item_name, chunk_excerpt, bin_title}`; populated only for assistant messages |
| `bin_ids_used`   | UUID[]                  | Bins active when this message was sent                                                  |
| `created_at`     | TIMESTAMPTZ             |                                                                                         |
| `updated_at`     | TIMESTAMPTZ             | Updated when a streamed assistant message is finalized                                  |

---

## 7. API Design

All routes (except health check and Clerk webhook endpoint) require a valid Clerk JWT in the `Authorization: Bearer <token>` header. The backend middleware extracts `user_id` from the JWT and passes it to each handler.

### 7.1 Auth and Webhook Routes

| Method | Path               | Description                                                                  |
| ------ | ------------------ | ---------------------------------------------------------------------------- |
| `GET`  | `/api/v1/auth/me`  | Returns the current user's Clerk profile                                     |
| `POST` | `/webhooks/clerk`  | Receives Clerk lifecycle events, verifies signature, and syncs `users` table |

Webhook subscriptions for `POST /webhooks/clerk`:

- `user.created`
- `user.updated`
- `user.deleted`

### 7.2 Bin Routes

| Method   | Path                 | Description                                           |
| -------- | -------------------- | ----------------------------------------------------- |
| `GET`    | `/api/v1/bins`          | List all bins for the current user                    |
| `POST`   | `/api/v1/bins`          | Create a new bin                                      |
| `GET`    | `/api/v1/bins/{bin_id}` | Get a single bin with its stats                       |
| `PATCH`  | `/api/v1/bins/{bin_id}` | Update bin title or description                       |
| `DELETE` | `/api/v1/bins/{bin_id}` | Delete bin, all its items, and its Pinecone namespace |

### 7.3 Ingestion and Item Routes

| Method   | Path                                               | Description                                             |
| -------- | -------------------------------------------------- | ------------------------------------------------------- |
| `POST`   | `/api/v1/bins/{bin_id}/items/upload`              | Upload one file and queue async ingestion               |
| `POST`   | `/api/v1/bins/{bin_id}/items/text`                | Add pasted text and queue async ingestion               |
| `GET`    | `/api/v1/bins/{bin_id}/items/{item_id}/ingestion-status` | Get latest ingestion status for an item                 |
| `GET`    | `/api/v1/ingestion/jobs/{job_id}`                 | Get ingestion status for a specific job                 |
| `GET`    | `/api/v1/ingestion/failures`                      | List recent ingestion failures for the authenticated user |
| `GET`    | `/api/v1/ingestion/metrics`                       | Get ingestion counters, timers, and recent failures     |

### 7.4 Chat Routes

| Method   | Path                                           | Description                                                                     |
| -------- | ---------------------------------------------- | ------------------------------------------------------------------------------- |
| `GET`    | `/api/v1/sessions`                             | List chat sessions for the current user                                         |
| `POST`   | `/api/v1/sessions`                             | Create a new chat session with optional bin IDs                                 |
| `GET`    | `/api/v1/sessions/{session_id}`                | Get a chat session                                                               |
| `DELETE` | `/api/v1/sessions/{session_id}`                | Delete a chat session                                                            |
| `PATCH`  | `/api/v1/sessions/{session_id}/bins`           | Update `last_active_bin_ids`; takes effect from the next message                |
| `GET`    | `/api/v1/sessions/{session_id}/messages`       | List messages for a chat session                                                 |
| `POST`   | `/api/v1/sessions/{session_id}/messages`       | Create a message on an owned chat session                                        |
| `POST`   | `/api/v1/chat/sessions/{session_id}/message`   | Run a chat turn; non-streaming in Phase 4, upgraded to SSE in Phase 5          |
| `POST`   | `/api/v1/chat/sessions/{session_id}/message-sync` | Compatibility non-streaming chat-turn response                                  |

In Phase 4, the message endpoint is a protected `POST` route returning a finalized assistant payload. In Phase 5, this route is upgraded to stream SSE-formatted events over an HTTP response consumed via `fetch` streaming. The planned event types are:

- `token` — a single LLM output token (streamed live)
- `citations` — JSON payload with source citations (sent after generation completes)
- `done` — signals the end of the stream

### 7.5 History Views (Composed from Session Routes)

| Method   | Path                        | Description                                       |
| -------- | --------------------------- | ------------------------------------------------- |
| `GET`    | `/api/v1/sessions`                       | List all sessions (history source)                |
| `GET`    | `/api/v1/sessions/{session_id}/messages` | Get all messages in a past session                |
| `DELETE` | `/api/v1/sessions/{session_id}`          | Delete a session and all its messages             |

---

## 8. Self-RAG Pipeline

### 8.1 Pipeline Overview

The pipeline is a LangGraph `StateGraph`. Each node is a pure function operating on a shared `GraphState`. Conditional edges inspect state fields to route between nodes.

```
User message + selected bin IDs
         │
         ▼
  Retrieval decision
  (retrieve or skip?)
     │           │
  retrieve      skip ──────────────────────┐
     │                                     │
     ▼                                     │
  Multi-bin Pinecone retrieval             │
  (parallel async query per namespace)     │
     │                                     │
     ▼                                     │
  Relevance grader                         │
  (per-doc binary grade)                   │
     │            │                        │
  relevant    not relevant                 │
     │            │                        │
     │       Query rewriter                │
     │       + re-retrieve                 │
     │       (max 3 attempts)              │
     ▼            │                        │
  Answer generator ◄───────────────────────┘
  (RAG prompt or parametric)
     │
     ▼
  Hallucination grader
  (grounded in docs?)
     │            │
  grounded    not grounded
     │            │
     │       Regenerate with corrective prompt
     │       (max 2 retries)
     │
     ▼
  Final response + citations
```

### 8.2 Node Descriptions

#### Retrieval decision node

Determines whether the query requires document retrieval or can be answered directly. If no bins are selected, this node is bypassed and the pipeline goes straight to parametric generation. If bins are selected, this node may still return `skip`, and that specific response is labelled parametric.

- Provider: Cerebras (fast binary decision)
- Output: `"retrieve"` or `"skip"`

#### Multi-bin retrieval node

Queries all selected bins' Pinecone namespaces in parallel — one async request per namespace. Results from all namespaces are merged into a single ranked candidate list before being passed to the relevance grader.

- Embedding: resolved via `EmbeddingRouter` (same model used at ingest time)
- Top-k per namespace: 5 (configurable)
- Merge strategy: round-robin interleave across namespaces, preserve per-namespace score order, deduplicate by chunk ID

#### Relevance grader node

Each retrieved chunk is evaluated independently — does it contain information that helps answer this specific query? Chunks that fail are filtered out before generation.

- Provider: Cerebras
- Scoring: binary yes/no via structured output
- Threshold: if fewer than 50% of chunks pass → trigger query rewrite
- Rewrite guard: max 3 rewrites before forcing generation with whatever docs remain

#### Query rewriter node

Reformulates the original query to improve retrieval quality. Triggered when the relevance grader determines the retrieved documents are insufficient.

- Provider: OpenRouter
- Output: a rewritten query stored in state; used in place of the original on the next retrieval pass

#### Answer generator node

Generates the answer using the filtered relevant documents and the user query. In parametric mode (no bins / skip decision), the same node runs but without any retrieved context in the prompt.

- Provider: OpenRouter
- Streaming: tokens are streamed from the `POST` endpoint and parsed by the frontend fetch stream reader
- Citations: during generation, the node tracks which chunks were referenced; these become the citations payload

#### Hallucination grader node

Checks whether the generated answer is grounded in the retrieved documents — i.e., does it contain claims not supported by any chunk? Only runs when retrieval was performed (not in parametric mode).

- Provider: Cerebras
- Output: `"grounded"` or `"not_grounded"`
- On failure: the answer generator is retried up to 2 times. Each retry appends a system-level instruction to the prompt informing the model that its previous response contained claims not supported by the retrieved documents, and instructing it to answer strictly within the provided context. The retrieved documents remain unchanged between retries.

### 8.3 Multi-Bin Retrieval Strategy

When multiple bins are selected, the retrieval node sends parallel async queries to each bin's Pinecone namespace using the same query embedding — one request per namespace. Results are collected and merged as follows:

1. Each namespace returns its own top-k results with cosine similarity scores.
2. Results are sorted within each namespace by score descending.
3. Candidates are merged with round-robin interleaving across namespaces until top N (default N=10) is reached.
4. Duplicate chunk IDs are removed during merge.
5. Each candidate retains its `bin_id` and `item_name` so citations can link back to the correct bin and content item.

This approach means the user's combined knowledge base acts like a single index, but the source of each chunk remains traceable.

### 8.4 No-Bin Fallback Behaviour

When no bins are selected at the start of a message:

- The retrieval decision node is skipped.
- The pipeline goes directly to the answer generator with an empty documents list.
- The generator prompt signals parametric mode — no context is injected.
- The message is stored with `retrieval_mode = "parametric"` and an empty citations array.
- The frontend displays a visible "Parametric mode" badge on the assistant's reply, so the user always knows the answer is from the model's general knowledge rather than their documents.

When bins are selected and the retrieval decision node outputs `skip`, the response is also stored and displayed as `parametric`.

---

## 9. Embedding Layer

The embedding model is fully decoupled from the rest of the system. All embedding configuration lives under the `embedding` key in `config.yaml`. Swapping models requires a config change and a re-ingest — no code changes.

### Supported providers

| Provider | Model                        | Dimensions                           | Status            |
| -------- | ---------------------------- | ------------------------------------ | ----------------- |
| Ollama   | `qwen3-embedding:0.6b`       | 1024                                 | **MVP (default)** |
| Gemini   | `gemini-embedding-exp-03-07` | 3072 (matryoshka, can reduce to 768) | Upgrade path      |
| OpenAI   | `text-embedding-3-small`     | 1536                                 | Available         |

### Critical constraint

Every content item in a bin must be embedded with the same model. If the active embedding provider is changed in config, all existing bins must be re-ingested into a new Pinecone index. The application must be taken offline for re-ingestion during a provider migration.

### Pinecone index configuration per provider

| Provider                      | Metric | Dimensions |
| ----------------------------- | ------ | ---------- |
| Ollama / Qwen3 0.6b           | cosine | 1024       |
| Gemini Embedding 2            | cosine | 3072       |
| OpenAI text-embedding-3-small | cosine | 1536       |

---

## 10. LLM Routing Layer

All LLM calls in the pipeline are resolved through a single `LLMRouter` class. Each pipeline node has a designated primary provider defined in `config.yaml`. If the primary provider fails (any exception — auth error, rate limit, network timeout), the router automatically tries the next provider in the `fallback_chain`.

### Provider assignments

| Pipeline node        | Primary provider | Reason                         |
| -------------------- | ---------------- | ------------------------------ |
| Retrieval decision   | Cerebras         | Fast, cheap binary decision    |
| Relevance grader     | Cerebras         | Fast, cheap binary decision    |
| Query rewriter       | OpenRouter       | Needs reasoning capability     |
| Answer generator     | OpenRouter       | Main generation, needs quality |
| Hallucination grader | Cerebras         | Fast, cheap binary decision    |

### Supported LLM providers

| Provider   | Notes                                                                                |
| ---------- | ------------------------------------------------------------------------------------ |
| Cerebras   | Native API; `llama-3.3-70b`; ultra-fast inference                                    |
| OpenRouter | OpenAI-compatible API; wide model selection; `meta-llama/llama-3.1-8b-instruct:free` |
| OpenAI     | Direct; `gpt-4o-mini`; used as high-quality fallback                                 |
| Ollama     | Local; `llama3.2`; final fallback; no API key required                               |

### Fallback chain

The order is fully user-defined in `config.yaml`. The default is: Cerebras → OpenRouter → OpenAI → Ollama. To change it, edit the `fallback_chain` list. No code changes required.

---

## 11. Ingestion Pipeline

When a user adds content to a bin, the following steps occur asynchronously in a background task:

1. **Parse** — extract raw text from the file (PDF via PyMuPDF, docx via python-docx, txt as-is) or accept the pasted text directly.
2. **Chunk** — split into overlapping chunks using `RecursiveCharacterTextSplitter.from_tiktoken_encoder()` (chunk size: 512 tokens, overlap: 64 tokens) — token-aware splitting ensures consistent chunk sizes across languages and document types.
3. **Embed** — each chunk is embedded using the active `EmbeddingRouter` provider.
4. **Upsert** — chunks are upserted into the bin's Pinecone namespace with metadata: `{item_id, bin_id, user_id, item_name, chunk_index}`.
5. **Update status** — the content item's `ingest_status` is set to `ready` in PostgreSQL (or `error` with a message if any step fails).

### Vector deletion strategy

When a content item is deleted, its vectors are purged from Pinecone using a metadata filter delete: `delete(filter={"item_id": "<id>"})`. No chunk ID tracking is required in PostgreSQL because `item_id` is already stored as vector metadata during upsert (step 4). Bin-level deletion purges all vectors instantly by dropping the entire Pinecone namespace.

The frontend polls the item's status endpoint until `ready` or `error` is received, then updates the UI accordingly.

---

## 12. Frontend — Pages & Components

### Pages

| Route              | Page           | Description                                                              |
| ------------------ | -------------- | ------------------------------------------------------------------------ |
| `/`                | Landing / Home | Marketing page with sign-in CTA                                          |
| `/sign-in`         | Sign In        | Clerk-hosted sign-in                                                     |
| `/sign-up`         | Sign Up        | Clerk-hosted sign-up                                                     |
| `/dashboard`       | Dashboard      | Overview: recent sessions, bins summary                                  |
| `/bins`            | Bins List      | All bins with create/edit/delete controls                                |
| `/bins/:binId`     | Bin Detail     | Content items list, upload, paste, manage                                |
| `/chat`            | Chat           | New chat with bin picker sidebar                                         |
| `/chat/:sessionId` | Chat Session   | Resume an editable session; bin picker restored to last active selection |
| `/history`         | History        | List of all past sessions                                                |

### Key components

**Bin picker (sidebar)**
A collapsible panel listing all user bins as toggleable checkboxes. Shows bin title, item count, and ingest status badges. Includes a search/filter input for users with many bins. Selected bins are displayed as pills above the chat input.

**Retrieval mode badge**
Displayed on every assistant message. Shows "Grounded — [bin names]" in a teal badge when retrieval provided supporting docs, or "Parametric" in a gray badge when no bins were selected or retrieval was skipped.

**Citation panel**
Expands below a grounded message to show the source chunks — each citation card shows the bin name, content item name, and the relevant text excerpt.

**Content item uploader**
Drag-and-drop zone accepting PDF, `.txt`, `.docx`. Shows per-file progress: queued → processing → ready / error. Supports batch upload.

**Paste text modal**
Simple form with a name field and a large text area. Submits to the text content endpoint on confirm.

**Ingest status indicator**
A small status pill on each content item card: gray (queued), yellow (processing), green (ready), red (error with tooltip showing the error message).

---

## 13. Project Structure

```
self-rag-app/
│
├── frontend/                         # React (Vite)
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Bins.tsx
│   │   │   ├── BinDetail.tsx
│   │   │   ├── Chat.tsx
│   │   │   └── History.tsx
│   │   ├── components/
│   │   │   ├── BinPicker.tsx
│   │   │   ├── CitationPanel.tsx
│   │   │   ├── RetrievalModeBadge.tsx
│   │   │   ├── FileUploader.tsx
│   │   │   ├── PasteTextModal.tsx
│   │   │   └── IngestStatusBadge.tsx
│   │   ├── hooks/
│   │   │   ├── useBins.ts
│   │   │   ├── useChat.ts
│   │   │   └── useSSE.ts
│   │   ├── store/
│   │   │   └── chatStore.ts          # Zustand: active session, selected bins
│   │   └── lib/
│   │       └── api.ts                # Typed API client
│   └── package.json
│
├── backend/                          # FastAPI
│   ├── main.py                       # App entry point, router registration
│   ├── config.yaml                   # LLM + embedding provider config
│   ├── .env                          # Secrets (never committed)
│   │
│   ├── api/
│   │   ├── auth.py
│   │   ├── webhooks.py
│   │   ├── bins.py
│   │   ├── documents.py
│   │   ├── chat.py
│   │   └── history.py
│   │
│   ├── core/
│   │   ├── auth.py                   # Clerk JWT middleware
│   │   ├── database.py               # Async SQLAlchemy engine + session
│   │   └── config.py                 # Pydantic settings from .env
│   │
│   ├── models/                       # SQLAlchemy ORM models
│   │   ├── bin.py
│   │   ├── content_item.py
│   │   ├── chat_session.py
│   │   └── message.py
│   │
│   ├── schemas/                      # Pydantic request/response schemas
│   │   ├── bin.py
│   │   ├── content_item.py
│   │   ├── chat.py
│   │   └── message.py
│   │
│   ├── services/
│   │   ├── ingestion.py              # Parse → chunk → embed → upsert
│   │   ├── pinecone_service.py       # Namespace management, delete, query
│   │   └── chat_service.py           # Session creation, message persistence
│   │
│   ├── pipeline/                     # LangGraph Self-RAG pipeline
│   │   ├── state.py
│   │   ├── graph.py
│   │   ├── nodes/
│   │   │   ├── retrieval_decision.py
│   │   │   ├── retrieval.py
│   │   │   ├── relevance_grader.py
│   │   │   ├── query_rewriter.py
│   │   │   ├── answer_generator.py
│   │   │   └── hallucination_grader.py
│   │   └── edges.py
│   │
│   ├── router/
│   │   ├── llm_router.py
│   │   ├── embedding_router.py
│   │   └── exceptions.py
│   │
│   ├── migrations/                   # Alembic
│   │   └── versions/
│   │
│   └── requirements.txt
│
└── docker-compose.yml                # PostgreSQL + backend + frontend (dev)
```

---

## 14. Environment Variables

### Backend (`.env`)

```
# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/selfrag

# Clerk
CLERK_SECRET_KEY=sk_...
CLERK_PUBLISHABLE_KEY=pk_...
CLERK_WEBHOOK_SECRET=whsec_...

# Pinecone
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=self-rag-index

# LLM providers
CEREBRAS_API_KEY=...
OPENROUTER_API_KEY=...
OPENAI_API_KEY=...

# Embedding providers (only required when active in config.yaml)
GOOGLE_API_KEY=...           # required when embedding.active = gemini

# Ollama (required when running in Docker; defaults to http://localhost:11434)
OLLAMA_BASE_URL=http://ollama:11434
```

### Frontend (`.env`)

```
VITE_CLERK_PUBLISHABLE_KEY=pk_...
VITE_API_BASE_URL=http://localhost:8000
```

---

## 15. References & Docs

### Self-RAG

- Self-RAG original paper (Asai et al., 2023): https://arxiv.org/abs/2310.11511
- LangGraph Self-RAG tutorial: https://langchain-ai.github.io/langgraph/tutorials/rag/langgraph_self_rag/

### LangGraph & LangChain

- LangGraph documentation: https://langchain-ai.github.io/langgraph/
- LangGraph StateGraph API: https://langchain-ai.github.io/langgraph/reference/graphs/
- LangGraph conditional edges: https://langchain-ai.github.io/langgraph/how-tos/branching/
- LangChain docs: https://python.langchain.com/docs/introduction/
- LangChain structured output: https://python.langchain.com/docs/how_to/structured_output/
- LangChain text splitters: https://python.langchain.com/docs/concepts/text_splitters/

### Pinecone

- Pinecone docs: https://docs.pinecone.io/
- Namespaces guide: https://docs.pinecone.io/guides/indexes/use-namespaces
- Serverless quickstart: https://docs.pinecone.io/guides/get-started/quickstart
- LangChain Pinecone integration: https://python.langchain.com/docs/integrations/vectorstores/pinecone/
- Pinecone Python SDK: https://github.com/pinecone-io/pinecone-python-client

### Embedding models

- Qwen3 Embedding (HuggingFace): https://huggingface.co/Qwen/Qwen3-Embedding
- Qwen3 Embedding on Ollama: https://ollama.com/library/qwen3-embedding
- Gemini Embedding API: https://ai.google.dev/gemini-api/docs/embeddings
- LangChain Google GenAI embeddings: https://python.langchain.com/docs/integrations/text_embedding/google_generative_ai/
- LangChain Ollama embeddings: https://python.langchain.com/docs/integrations/text_embedding/ollama/

### LLM providers

- Cerebras API docs: https://inference-docs.cerebras.ai/introduction
- LangChain Cerebras integration: https://python.langchain.com/docs/integrations/chat/cerebras/
- OpenRouter quickstart: https://openrouter.ai/docs/quick-start
- OpenRouter model list: https://openrouter.ai/models
- OpenRouter with LangChain: https://openrouter.ai/docs/frameworks/langchain
- Ollama docs: https://github.com/ollama/ollama
- LangChain Ollama integration: https://python.langchain.com/docs/integrations/chat/ollama/

### FastAPI

- FastAPI docs: https://fastapi.tiangolo.com/
- FastAPI background tasks: https://fastapi.tiangolo.com/tutorial/background-tasks/
- FastAPI streaming responses (SSE): https://fastapi.tiangolo.com/advanced/custom-response/
- SQLAlchemy async with FastAPI: https://fastapi.tiangolo.com/tutorial/sql-databases/

### PostgreSQL & SQLAlchemy

- SQLAlchemy 2.0 async docs: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic docs: https://alembic.sqlalchemy.org/en/latest/
- asyncpg driver: https://magicstack.github.io/asyncpg/current/

### Authentication — Clerk

- Clerk docs: https://clerk.com/docs
- Clerk React SDK: https://clerk.com/docs/references/react/overview
- Clerk Python / FastAPI backend integration: https://clerk.com/docs/backend-requests/handling/manual-jwt
- Clerk webhooks overview: https://clerk.com/docs/webhooks/overview
- Clerk JWT verification: https://clerk.com/docs/backend-requests/resources/session-tokens

### Frontend

- React docs: https://react.dev/
- Vite docs: https://vitejs.dev/guide/
- TanStack Query: https://tanstack.com/query/latest
- Zustand: https://zustand-demo.pmnd.rs/
- Tailwind CSS: https://tailwindcss.com/docs/installation

### File parsing

- PyMuPDF (PDF extraction): https://pymupdf.readthedocs.io/en/latest/
- python-docx (.docx extraction): https://python-docx.readthedocs.io/en/latest/

---

_Document version: 2.0 — March 2026_
_Author: S.V. Sasank Varma — github.com/shasank0001_
