# Self-RAG Knowledge Chat

> A full-stack AI chat app where users organise personal knowledge into named **Bins**, select bins per chat session, and get answers grounded in their own documents via a Self-RAG pipeline — with explicit fallback to LLM parametric memory when retrieval is not used.

## Table of Contents

1. [What This Is](#1-what-this-is)
2. [Features](#2-features)
3. [Architecture](#3-architecture)
4. [Tech Stack](#4-tech-stack)
5. [Repository Structure](#5-repository-structure)
6. [Prerequisites](#6-prerequisites)
7. [Quick Start](#7-quick-start)
8. [Backend / Frontend Setup (manual)](#8-backend--frontend-setup-manual)
9. [Clerk Auth + Webhook Setup](#9-clerk-auth--webhook-setup)
10. [API Reference](#10-api-reference)
11. [Self-RAG Pipeline](#11-self-rag-pipeline)
12. [Ingestion Pipeline](#12-ingestion-pipeline)
13. [Frontend Routes](#13-frontend-routes)
14. [Streaming (SSE) Notes](#14-streaming-sse-notes)
15. [Observability and Hardening](#15-observability-and-hardening)
16. [Testing](#16-testing)
17. [Deployment Notes](#17-deployment-notes)
18. [References](#18-references)

---

## 1. What This Is

Users create private knowledge bases called **Bins**. Each bin holds uploaded files (PDF, `.txt`, `.docx`) and pasted text. During chat, the user selects one or more bins. A LangGraph Self-RAG pipeline decides whether to retrieve, fetches relevant chunks from the selected bins' Pinecone namespaces, grades relevance, rewrites weak queries, generates a grounded answer with citations, and checks for hallucinations before responding.

If no bins are selected — or the decision node returns `skip` — the system answers from parametric LLM knowledge and labels it **Parametric** in the UI. Grounded answers are labelled **Grounded** with expandable citations.

Goals: fine-grained control over what knowledge the AI draws from; folder-like bin UX; honesty (every answer is grounded or explicitly labelled parametric); provider swaps via config, not code rewrites.

---

## 2. Features

- **Auth (Clerk only):** hosted sign-in/sign-up, backend validates Clerk JWTs per request, `users` mirrored via idempotent Svix webhooks (`user.created/updated/deleted`). See [§9](#9-clerk-auth--webhook-setup).
- **Bins:** create (title + optional description), list, rename, delete (DB cascade + Pinecone namespace purge). Home page (`/`) is the bins list.
- **Content:** file upload (PDF/TXT/DOCX, per-file size cap, batch) with `queued → processing → ready / error` visibility; pasted-text items; per-item delete (DB + Pinecone metadata-filter purge); bin stats (items, chunks, storage).
- **Chat:** new session, multi-select bin picker, change bins mid-session (effective next message, persisted), live token streaming, `Grounded` vs `Parametric` badges, citation panel with source excerpts, thinking-step visibility while the graph runs.
- **History:** auto-saved sessions, chronological list, read-only detail at `/history/:sessionId`, editable resume at `/chat/:sessionId`, session delete (messages only, bins untouched).
- **Resumable streams:** SSE cursor (`assistant_message_id:sequence_number`) + `Last-Event-ID` support; see `docs/phase5_sse_contract.md`.

---

## 3. Architecture

```text
React (Vite, TS) frontend
  │  authenticated REST + streaming POST (fetch + ReadableStream)
  ▼
FastAPI backend (/api/v1)
  │  Clerk JWT auth → SQLAlchemy 2 (async) + Postgres (app state)
  │  LangGraph Self-RAG orchestration (retrieve → grade → rewrite → generate → verify)
  │  Embedding + LLM provider routing (OpenRouter default, Pinecone, Ollama fallback)
  ▼
Pinecone vector store (one namespace per bin)
```

Key decisions:

- **One Pinecone namespace per bin** (`namespace == bin_id`). Multi-bin retrieval queries selected namespaces and merges results; bin delete purges the namespace.
- **Async ingestion:** upload returns immediately with queued status; a worker does parse → chunk → embed → upsert and updates status. Frontend polls until terminal.
- **Streaming over POST:** `POST /api/v1/chat/sessions/{session_id}/message` streams SSE frames (`thinking`, `token`, `citations`, `done`, `error`) so the `Authorization` header can be sent (native `EventSource` is GET-only).
- **Clerk owns auth:** backend stores no passwords; `get_or_create_user_from_claims` lazily provisions local users so first-login requests never hit FK violations.

---

## 4. Tech Stack

| Layer        | Technology | Notes |
|---|---|---|
| Frontend     | React 19 + TypeScript, Vite, Tailwind v4 | `pnpm`, TanStack Query (server state), Zustand (ephemeral UI), Clerk React SDK |
| Backend      | FastAPI, SQLAlchemy 2 (async) + asyncpg, Alembic, Pydantic v2 | `uv`, structured JSON logging, OpenTelemetry optional, Prometheus `/metrics` |
| Vector store | Pinecone (namespace per bin) | host- or index-name-based connection |
| Orchestration| LangGraph (`StateGraph` + conditional edges) | versioned prompt templates under `backend/app/pipeline/prompts/` |
| Embeddings (current default) | OpenRouter `openai/text-embedding-3-small`, 1536-dim | re-ingest required on model/provider change |
| Generation   | OpenRouter (OpenAI-compatible) + fallbacks | heartbeat + retry envs, see [§14](#14-streaming-sse-notes) |
| Auth         | Clerk (JWT + Svix webhooks) | JWKS-verified, raw-body signature check, `svix-id` dedupe |
| Infra        | Docker Compose (Postgres 17 + backend), Grafana/Prometheus assets, `ops/` runbooks | `infra/docker/backend.Dockerfile` |

---

## 5. Repository Structure

```text
.
├── backend/
│   ├── app/
│   │   ├── api/v1/routes/   # auth, bins, chat, health, ingestion, messages,
│   │   │                   # observability, sessions, webhooks
│   │   ├── auth/            # Clerk JWT verification (JWKS)
│   │   ├── core/            # config, errors, logging, tracing, pipeline_config
│   │   ├── db/              # engine/session, base models
│   │   ├── models/          # users, bins, items, sessions, messages, jobs, telemetry
│   │   ├── pipeline/        # graph, state, nodes/*, prompts/*, evals/
│   │   ├── router/          # llm_router, embedding_router
│   │   ├── services/        # chat, ingestion/*, authorization, webhooks, usage
│   │   └── main.py
│   ├── alembic/             # migrations + versions/
│   ├── config.yaml          # pipeline/provider routing
│   ├── scripts/             # seed_phase1.py, check_db.py, run_phase4_evals.py
│   └── tests/               # phase-organised suites + evals
├── frontend/
│   └── src/
│       ├── pages/           # BinList, Chat, History, Settings, SignIn, SignUp
│       ├── components/      # BinPicker, CitationPanel, MessageContent,
│       │                   # RetrievalModeBadge, ThinkingPanel
│       ├── hooks/           # useChat, useSSE
│       ├── lib/             # api/client, auth, stream/sse
│       ├── store/chatStore.ts
│       └── routes.tsx
├── docs/                    # phase5_sse_contract.md, pipeline_change_summary.md,
│                            # runbooks/*, perf/*
├── infra/docker/            # backend.Dockerfile
├── ops/                     # db backup/restore, monitoring, policies
├── tests/perf/k6/           # chat + ingestion load scripts
├── project_overview.md      # full product spec
└── docker-compose.yml
```

Planning docs (`plan.md`, `phase*_plan.md`) are intentionally gitignored (`**/*_plan.md`); `project_overview.md` is the tracked spec.

---

## 6. Prerequisites

- Python 3.12+
- `uv`
- Node.js 20+
- `pnpm`
- Docker + Docker Compose

## Frontend Standards

- Every frontend task must use the frontend design skill (`frontend-design`) during implementation.
- For chat UI work, use `reference_images/chat_interface.png` as the visual reference baseline for layout, composition, and interaction density.
- Use `web-design-reviewer` for visual QA/review passes when UI changes are substantial.
- UI direction should stay clean, simple, and minimal.
- Prefer clear hierarchy, generous spacing, restrained color usage, and low visual noise over decorative styling.
- Keep responsive behavior predictable on desktop and mobile.

---

## 7. Quick Start

1. Copy environment files:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

2. Start Postgres:

```bash
docker compose up -d postgres
```

If port `5432` is already in use:

```bash
POSTGRES_PORT=5433 docker compose up -d postgres
```

3. Install dependencies:

```bash
make setup
```

4. Run migrations:

```bash
make db-upgrade
```

Alternate port example:

```bash
ALEMBIC_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/selfrag \
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/selfrag \
make db-upgrade
```

5. Start backend and frontend:

```bash
make dev-backend
make dev-frontend
```

---

## 8. Backend / Frontend Setup (manual)

```bash
cd backend
uv venv
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
pnpm install
cp .env.example .env.local
pnpm dev
```

If `pnpm` is not installed globally: `corepack pnpm install` / `corepack pnpm dev`.

---

## 9. Clerk Auth + Webhook Setup

1. In Clerk Dashboard, create an application and collect publishable key (`pk_...`), secret key (`sk_...`), and issuer URL for `CLERK_ISSUER`.
2. Backend env (`backend/.env`): `CLERK_ISSUER`, `CLERK_JWKS_URL` (optional, derived from issuer when omitted), `CLERK_AUDIENCE` (optional), `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`.
3. Frontend env (`frontend/.env.local`): `VITE_CLERK_PUBLISHABLE_KEY`.
4. Protected API routes require `Authorization: Bearer <clerk_session_token>`.
5. Restart the backend after changing Clerk env vars.

Webhooks (Svix):

1. Expose the backend (e.g. `cloudflared tunnel --url http://localhost:8000`).
2. Add Clerk webhook endpoint `<your-public-url>/webhooks/clerk`.
3. Set `CLERK_WEBHOOK_SECRET` (`whsec_...`) in `backend/.env`.
4. Events `user.created/updated/deleted` are processed idempotently: signature verification uses raw request bytes + `svix-id`/`svix-timestamp`/`svix-signature`, and duplicate deliveries (same `svix-id`) are ignored.

---

## 10. API Reference

All `/api/v1/*` routes (except health/readiness and `/webhooks/clerk`) require `Authorization: Bearer <Clerk JWT>`. Webhooks mount at the root: `POST /webhooks/clerk`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/health`, `/api/v1/ready` | Liveness / readiness (DB check) |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/api/v1/auth/me` | Current user identity |
| `POST` | `/webhooks/clerk` | Clerk lifecycle events (Svix-verified) |
| `GET`/`POST` | `/api/v1/bins` | List / create bins |
| `GET`/`DELETE` | `/api/v1/bins/{bin_id}` | Bin detail + stats / delete (DB + namespace purge) |
| `POST` | `/api/v1/bins/{bin_id}/items/upload` | File upload, queues ingestion |
| `POST` | `/api/v1/bins/{bin_id}/items/text` | Pasted text, queues ingestion |
| `GET` | `/api/v1/bins/{bin_id}/items/{item_id}/ingestion-status` | Item ingest status |
| `GET` | `/api/v1/ingestion/jobs/{job_id}` | Job status |
| `GET` | `/api/v1/ingestion/failures`, `/api/v1/ingestion/metrics` | Failure list / counters |
| `GET`/`POST` | `/api/v1/sessions` | List / create chat sessions |
| `GET`/`DELETE` | `/api/v1/sessions/{session_id}` | Session detail / delete |
| `PATCH` | `/api/v1/sessions/{session_id}` | Update active bins (`last_active_bin_ids`) |
| `GET` | `/api/v1/sessions/{session_id}/messages` | Session messages |
| `POST` | `/api/v1/chat/sessions/{session_id}/message` | Chat turn, SSE stream (new or `cursor` resume) |
| `GET` | `/api/v1/observability/usage-rollups` | Usage aggregates |

---

## 11. Self-RAG Pipeline

LangGraph `StateGraph` over a typed state (message, session/user/bin IDs, rewritten query, retrieved chunks, citations, `retrieval_mode`, attempt counters, final answer).

```text
message + bin_ids → retrieval decision (retrieve | skip; bypass if no bins)
  retrieve → multi-bin Pinecone retrieval (parallel per-namespace)
           → relevance grader (per-chunk yes/no)
             weak set → query rewriter + re-retrieve (bounded attempts)
           → answer generator (grounded prompt + citations, or parametric prompt)
  skip ──────────────→ answer generator (empty docs, parametric mode)
           → hallucination grader (grounded answers only)
             not grounded → regenerate with corrective feedback (bounded retries)
           → final response + citations
```

- **No-bin / skip fallback:** decision node bypassed or `skip` → generator runs with empty docs → stored and displayed as `parametric` with empty citations.
- **Bounded loops:** rewrite and regeneration retries both have configured ceilings — the graph always terminates with the best available answer.
- **Prompts are versioned:** `backend/app/pipeline/prompts/templates/<node>/v1.txt` via a prompt registry; evals pin prompt versions per case.
- **Routing/fallbacks:** `backend/config.yaml` + `LLMRouter`/`EmbeddingRouter`; provider failures fall back along the configured chain (fail-fast on auth/config errors, retry on transient 429/5xx).

---

## 12. Ingestion Pipeline

1. **Parse** — PDF via PyMuPDF, DOCX (paragraphs + tables + headings), TXT passthrough; whitespace normalisation; unsupported types rejected.
2. **Chunk** — token-aware overlapping splitter; deterministic chunk IDs (`{item_id}:{chunk_index}` + content hash) so re-ingest is stable.
3. **Embed** — active `EmbeddingRouter` provider (same model family used for query embeddings at retrieval).
4. **Upsert** — into the bin's Pinecone namespace with `{item_id, bin_id, user_id, item_name, chunk_index}` metadata (batched, within provider payload limits).
5. **Status** — `queued → processing → ready` (or `error` with a user-safe message; full tracebacks stay server-side). Frontend polls status endpoints until terminal.

Deletion: item vectors purged by `item_id` metadata filter; bin deletion purges the whole namespace. Changing the embedding model/provider requires re-ingesting affected bins (stored dimensions must stay aligned) — see `backend/config.yaml`.

---

## 13. Frontend Routes

Actual routes (`frontend/src/routes.tsx`; `/` requires auth):

| Route | Page | Notes |
|---|---|---|
| `/` | Bins list (`BinListPage`) | index route, bin cards |
| `/chat` | New chat (`ChatPage`) | bin picker sidebar |
| `/chat/:sessionId` | Resume (editable) | restores last active bins |
| `/history` | History list (`HistoryPage`) | chronological sessions |
| `/history/:sessionId` | History detail (read-only) | no composer; "continue in chat" link |
| `/settings` | Settings (`SettingsPage`) | provider/keys UI |
| `/sign-in`, `/sign-up` | Clerk hosted | public |

Data flow: TanStack Query owns server state (bins, items, sessions, messages, ingest polling); Zustand holds ephemeral UI (picker collapsed, composer draft, streaming tokens).

---

## 14. Streaming (SSE) Notes

- Streaming endpoint: `POST /api/v1/chat/sessions/{session_id}/message`
- Full wire contract: `docs/phase5_sse_contract.md` (event envelope, `thinking`/`token`/`citations`/`done`/`error`, cursor resume, `Last-Event-ID`).
- New stream: send `{message, bin_ids?}`. Resume: send `{cursor}` (`assistant_message_id:sequence_number`).
- Deep research: send `research: true` with `message` and at least one selected bin. The graph plans up to 3 sub-questions (bounded by `pipeline.research` config), retrieves per sub-question with one rewrite hop each, then synthesizes a structured report with per-claim citations. Progress streams as `thinking` events (`research_planner`, `research_executor`, `research_synthesizer`).
- Backend stream env: `CHAT_STREAM_HEARTBEAT_INTERVAL_MS=10000`
- Frontend retry env: `CHAT_STREAM_RETRY_MAX_ATTEMPTS=5`, `CHAT_STREAM_RETRY_BASE_MS=500`

---

## 15. Observability and Hardening

- Structured JSON logging with correlation context (`request_id`, `chat_id`, `job_id`, `bin_id`, `provider`, `model`, `fallback_attempt`).
- OpenTelemetry: `OTEL_TRACING_ENABLED=true`, `OTEL_EXPORTER_OTLP_ENDPOINT=<collector-url>`, `OTEL_SERVICE_NAME=self-rag-backend`.
- Prometheus: `GET /metrics`; alerts in `ops/monitoring/prometheus/alerts_phase6.yml`; dashboard in `ops/monitoring/grafana/`.
- Usage: `GET /api/v1/observability/usage-rollups`.
- Rate limiting + request-size protections on by default. Never log secrets, JWTs, or raw file bytes. Runbooks: `docs/runbooks/` (backup/restore drill, migration safety, release checklist, security ops, SLO/alerts).

---

## 16. Testing

```bash
make test            # full suite entrypoint
cd frontend && pnpm build
```

Backend suites under `backend/tests/` (phase-organised: auth/webhooks, ingestion, RAG pipeline, router, prompts, sessions/history, streaming, observability, security, vector index) plus eval harness (`backend/scripts/run_phase4_evals.py`, golden dataset in `backend/tests/evals/`). Load scripts: `tests/perf/k6/`. Seed/verify: `uv run python scripts/seed_phase1.py`, `uv run python scripts/check_db.py`.

Migrations:

```bash
make db-revision m="describe change"
make db-upgrade
make db-downgrade
```

---

## 17. Deployment Notes

- Order: Postgres → backend (`alembic upgrade head`) → frontend → Pinecone/provider keys.
- Compose: `docker compose up -d postgres` (or `--profile ollama` for local fallback); backend builds from `infra/docker/backend.Dockerfile`.
- Embedding swap = new index dims + offline re-ingest; never mix dimensions in one index.
- Uploaded bytes live outside git (`data/` is gitignored); back up Postgres (`ops/db/backup.sh`) and the uploads volume.
- Keep webhook endpoint public + Svix-verified; all `/api/v1/*` app routes JWT-protected.

---

## 18. References

- Full product spec: `project_overview.md`
- Streaming contract: `docs/phase5_sse_contract.md`
- Pipeline changes: `docs/pipeline_change_summary.md`
- Runbooks: `docs/runbooks/`
- Self-RAG paper (Asai et al., 2023): https://arxiv.org/abs/2310.11511
- LangGraph: https://langchain-ai.github.io/langgraph/
- Pinecone namespaces: https://docs.pinecone.io/guides/indexes/use-namespaces
- Clerk JWT handling: https://clerk.com/docs/backend-requests/handling/manual-jwt
- Clerk webhooks: https://clerk.com/docs/webhooks/overview
