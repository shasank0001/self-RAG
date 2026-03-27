# Self-RAG Project Explanation

This document explains the project in simple terms so a new developer can understand the system quickly.

## 1. What this project is

Self-RAG is a full-stack AI chat app where each user can:

- Create private knowledge bins
- Upload files or paste text into those bins
- Ask questions in chat
- Get answers grounded in their own content when retrieval is used

If retrieval is not used (or no bins are selected), the app clearly labels the response as parametric model knowledge.

## 2. Main idea in one paragraph

Users sign in with Clerk, manage their own bins of content, and chat with an assistant. On each chat message, a LangGraph-based Self-RAG pipeline decides whether to retrieve from selected bins. If retrieval runs, relevant chunks are fetched from Pinecone and used for grounded generation with citations. If retrieval is skipped, the model answers from parametric knowledge and the UI marks that mode explicitly.

## 3. High-level architecture

```text
React (Vite, TS) frontend
  -> authenticated REST + streaming POST
FastAPI backend
  -> Clerk JWT validation
  -> SQLAlchemy + Postgres (app state)
  -> LangGraph Self-RAG orchestration
  -> Embedding + LLM provider routing
Pinecone vector store
  -> one namespace per bin
```

## 4. Core concepts

### User

Authenticated through Clerk. Backend trusts Clerk JWTs and maps users into a local users table.

### Bin

A private collection of knowledge items for one user. Each bin maps to its own Pinecone namespace.

### Content item

A file upload (PDF, TXT, DOCX) or pasted text. Content is chunked, embedded, and indexed.

### Ingestion job

Background processing lifecycle for content indexing (queued/running/succeeded/failed style states).

### Chat session

A conversation thread tied to user and selected bins. Messages and metadata are saved in Postgres.

### Retrieval mode

- Grounded mode: retrieved chunks are used.
- Parametric mode: no retrieval; answer comes from model memory.

## 5. End-to-end flow

1. User signs in with Clerk.
2. User creates bins and adds files/text.
3. Backend parses and chunks content.
4. Chunks are embedded and upserted into Pinecone namespace for that bin.
5. User opens chat and selects bins.
6. For each message, Self-RAG decides retrieve or skip.
7. If retrieve:
   - query selected bin namespaces
   - rank/filter chunks
   - generate answer with citations
8. If skip:
   - generate parametric answer
9. Response is streamed to frontend with SSE-style events over POST.
10. Session and messages are persisted for history.

## 6. Backend responsibilities

Main backend stack:

- FastAPI
- SQLAlchemy 2 (async)
- Alembic migrations
- Postgres + asyncpg
- LangGraph for Self-RAG flow
- Pinecone for vector search

Backend handles:

- Auth verification (Clerk JWT)
- Webhook sync from Clerk (Svix signature verification)
- Bin/content CRUD
- Async ingestion
- Chat orchestration and streaming
- Observability endpoints and metrics

Important backend endpoints in current docs:

- POST /webhooks/clerk
- POST /api/v1/chat/sessions/{session_id}/message
- GET /metrics
- GET /api/v1/observability/usage-rollups

## 7. Frontend responsibilities

Main frontend stack:

- React 19 + TypeScript
- Vite
- Tailwind v4
- TanStack Query
- Zustand
- Clerk React SDK

Frontend handles:

- Auth screens and session state
- Bin management UI
- Upload progress and ingest status
- Chat UI with streamed assistant messages
- History navigation and session resume

## 8. Data model (simplified)

Core relational entities:

- users
- bins
- content items/documents
- ingestion jobs
- chat sessions
- chat messages
- observability/usage records

Key rules:

- Everything is scoped by user ownership.
- Bin deletion removes associated vectors from Pinecone.
- Chat session keeps selected-bin context for reproducible history.

## 9. Local development quick start

From repo root:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
docker compose up -d postgres
make setup
make db-upgrade
make dev-backend
make dev-frontend
```

If port 5432 is busy:

```bash
POSTGRES_PORT=5433 docker compose up -d postgres
```

If database is on port 5433, use override URLs for migration commands:

```bash
ALEMBIC_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/selfrag \
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/selfrag \
make db-upgrade
```

## 10. Common commands

```bash
make setup
make setup-backend
make setup-frontend
make dev-backend
make dev-frontend
make db-revision m="describe change"
make db-upgrade
make db-downgrade
make test
```

Frontend production build:

```bash
cd frontend && pnpm build
```

## 11. Key folders

```text
backend/
  app/            FastAPI app code (api, services, pipeline, models)
  alembic/        migrations
  tests/          backend tests

frontend/
  src/            React app code (pages, components, hooks, lib)

docs/             contracts, ADRs, runbooks
monitoring/       Grafana/Prometheus assets
ops/              operational scripts (backup/restore)
```

## 12. Project status snapshot

Based on repository plans and README:

- Foundation, auth, ingestion, Self-RAG flow, and streaming are implemented.
- Observability and hardening assets are present (metrics, runbooks, policies).
- Backend test suite exists and is phase-organized.
- Frontend has dev/build scripts; no dedicated frontend test script is currently configured.

## 13. What to change when extending features

### Add a new backend feature

1. Add/adjust Pydantic schemas in API layer.
2. Add service-layer logic in backend app services.
3. Add/adjust SQLAlchemy models if needed.
4. Create Alembic migration for schema changes.
5. Add tests in backend/tests.

### Add a new retrieval behavior

1. Update LangGraph node logic in pipeline modules.
2. Keep response schema stable for frontend streaming parser.
3. Add targeted tests for routing and fallback behavior.

### Add a new frontend screen

1. Add route/page/component in frontend/src.
2. Use TanStack Query for server state.
3. Keep chat stream behavior compatible with SSE event contract.

## 14. Operational notes

- Do not log secrets or raw sensitive payloads.
- Preserve request tracing and structured logs.
- For schema updates, always generate and review Alembic migration.
- When changing embedding dimensions/model, re-ingest existing vectors to keep index compatibility.

## 15. One-minute mental model

Think of this system as three connected layers:

1. Product layer (React): bins, chat, history.
2. Orchestration layer (FastAPI + LangGraph): decide retrieve vs skip, generate answer, stream events.
3. Knowledge layer (Postgres + Pinecone): metadata in Postgres, semantic chunks in Pinecone namespaces per bin.

If you understand these three layers and the message flow between them, you understand the project.
