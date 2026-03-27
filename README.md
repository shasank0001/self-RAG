# Self-RAG Knowledge Chat

Phase 3 implementation is in place for ingestion, indexing, and operational status visibility across backend and frontend.

## Frontend Standards

- Every frontend task must use the frontend design skill (`frontend-design`) during implementation.
- For chat UI work, use `reference_images/chat_interface.png` as the visual reference baseline for layout, composition, and interaction density.
- Use `web-design-reviewer` for visual QA/review passes when UI changes are substantial.
- UI direction should stay clean, simple, and minimal.
- Prefer clear hierarchy, generous spacing, restrained color usage, and low visual noise over decorative styling.
- Keep responsive behavior predictable on desktop and mobile.

## Prerequisites

- Python 3.12+
- `uv`
- Node.js 20+
- `pnpm`
- Docker + Docker Compose

## Quick Start

1. Copy environment files:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

2. Start Postgres:

```bash
docker compose up -d postgres
```

If port `5432` is already in use, start Postgres on an alternate host port:

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

If Postgres is running on an alternate port (for example `5433`), run:

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

## Backend Setup (manual)

```bash
cd backend
uv venv
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Frontend Setup (manual)

```bash
cd frontend
pnpm install
cp .env.example .env.local
pnpm dev
```

If `pnpm` is not installed globally, use:

```bash
corepack pnpm install
corepack pnpm dev
```

## Clerk Auth Setup

1. In Clerk Dashboard, create an application and collect:
   - Publishable key (`pk_...`)
   - Secret key (`sk_...`)
   - Frontend API / issuer URL for `CLERK_ISSUER`
2. Configure backend env (`backend/.env`):
   - `CLERK_ISSUER`
   - `CLERK_JWKS_URL` (optional, derived automatically from `CLERK_ISSUER` when omitted)
   - `CLERK_AUDIENCE` (optional, if your JWT template uses `aud`)
   - `CLERK_SECRET_KEY`
   - `CLERK_PUBLISHABLE_KEY`
3. Configure frontend env (`frontend/.env.local`):
   - `VITE_CLERK_PUBLISHABLE_KEY`
4. Protected API routes now require `Authorization: Bearer <clerk_session_token>`.
5. Restart the backend after changing Clerk env vars so the verifier picks up the latest settings.

## Clerk Webhook Setup (Svix)

1. Expose your backend to Clerk locally using a tunnel (example with `cloudflared`):

```bash
cloudflared tunnel --url http://localhost:8000
```

2. In Clerk Dashboard Webhooks, add endpoint:
   - `<your-public-url>/webhooks/clerk`
3. Copy the webhook signing secret (`whsec_...`) into `backend/.env` as `CLERK_WEBHOOK_SECRET`.
4. Supported lifecycle events are processed idempotently:
   - `user.created`
   - `user.updated`
   - `user.deleted`

Notes:
- Signature verification uses raw request bytes and Svix headers (`svix-id`, `svix-timestamp`, `svix-signature`).
- Duplicate webhook deliveries (same `svix-id`) are ignored safely.

## Migrations

```bash
make db-revision m="init phase1"
make db-upgrade
make db-downgrade
```

## Build / Verify

```bash
cd frontend
pnpm build
```

Fallbacks when `pnpm` is unavailable:

```bash
cd frontend
corepack pnpm build
# or
npm run build
```

## Phase 6 Observability and Hardening

- Structured JSON logging with correlation context (`request_id`, `chat_id`, `job_id`, `bin_id`, `provider`, `model`, `fallback_attempt`) is enabled by default.
- OpenTelemetry tracing can be configured with:
  - `OTEL_TRACING_ENABLED=true`
  - `OTEL_EXPORTER_OTLP_ENDPOINT=<collector-url>`
  - `OTEL_SERVICE_NAME=self-rag-backend`
- Prometheus metrics endpoint:
  - `GET /metrics`
- Usage rollups endpoint:
  - `GET /api/v1/observability/usage-rollups`
- Rate limiting and request-size protections are enabled by default.

## Embeddings (Current Default)

- Provider: `openrouter`
- Model: `openai/text-embedding-3-small`
- Default dimensions: `1536`

Notes:
- Set `OPENROUTER_API_KEY` in `backend/.env`.
- OpenRouter uses an OpenAI-compatible embeddings API, so no local model pull is needed.
- Changing the embedding model/provider requires re-ingesting existing bins because stored vector dimensions must stay aligned.

## Pinecone + OpenRouter Setup

- Pinecone required:
  - `PINECONE_API_KEY`
  - and one of:
    - `PINECONE_HOST` (recommended for production), or
    - `PINECONE_INDEX_NAME`
- OpenRouter required:
  - `OPENROUTER_API_KEY`
  - optional override: `OPENROUTER_BASE_URL` (default `https://openrouter.ai/api/v1`)

Notes:
- For Pinecone host-based connections, you can use your index host directly (without protocol is preferred).
- If both `PINECONE_HOST` and `PINECONE_INDEX_NAME` are set, host is used.

## Phase 5 Streaming Notes

- Streaming endpoint: `POST /api/v1/chat/sessions/{session_id}/message`
- SSE contract reference: `docs/phase5_sse_contract.md`
- Backend stream env:
  - `CHAT_STREAM_HEARTBEAT_INTERVAL_MS=10000`
- Frontend retry env:
  - `CHAT_STREAM_RETRY_MAX_ATTEMPTS=5`
  - `CHAT_STREAM_RETRY_BASE_MS=500`

## Seed / DB Check

```bash
cd backend
uv run python scripts/seed_phase1.py
uv run python scripts/check_db.py
```
