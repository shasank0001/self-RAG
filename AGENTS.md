# AGENTS.md

This file defines repository-specific guidance for agentic coding assistants.
It is based on current code and markdown docs in this repo.

## 1) Project Snapshot
- Monorepo with backend (`backend/`) and frontend (`frontend/`).
- Backend stack: Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, asyncpg, pytest.
- Frontend stack: React 19, Vite, TypeScript strict, Tailwind v4, TanStack Query, Zustand.
- Local infra: Postgres via root `docker-compose.yml`.

## 2) Rule Files (Cursor/Copilot)
- Checked `.cursorrules`: not present.
- Checked `.cursor/rules/`: not present.
- Checked `.github/copilot-instructions.md`: not present.
- If any of these files appear later, treat them as higher-priority instructions and update this file.

## 3) Canonical Commands
Run from repo root unless noted.

### Setup
- `make setup`
- `make setup-backend`
- `make setup-frontend`

### Local development
- Start Postgres: `docker compose up -d postgres`
- If port 5432 is occupied: `POSTGRES_PORT=5433 docker compose up -d postgres`
- Start backend: `make dev-backend`
- Start frontend: `make dev-frontend`

### Database / migrations
- Create migration: `make db-revision m="describe change"`
- Apply migrations: `make db-upgrade`
- If Postgres runs on non-default host port (e.g. 5433), override URLs when running migrations:
  - `ALEMBIC_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/selfrag DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/selfrag make db-upgrade`
- Roll back one migration: `make db-downgrade`

### Build
- Frontend production build: `cd frontend && pnpm build`
- Fallback when `pnpm` is unavailable: `cd frontend && corepack pnpm build` (or `npm run build`)
- Backend currently has no separate package build target.

### Tests
- Full backend tests: `make test`
- Equivalent direct command: `cd backend && uv run pytest`
- Frontend test script is not configured yet.

### Single test (important)
- Single file: `cd backend && uv run pytest tests/test_example.py`
- Single class: `cd backend && uv run pytest tests/test_example.py::TestClass`
- Single test: `cd backend && uv run pytest tests/test_example.py::test_case`
- Name filter: `cd backend && uv run pytest -k "keyword"`
- Quiet mode: `cd backend && uv run pytest -q`
- Fallback without `uv`: `cd backend && python3 -m pytest ...`

### Lint/format/typecheck status
- No explicit backend lint config (`ruff`, `black`, `isort`, `mypy`) is committed yet.
- No frontend `lint` script is defined in `frontend/package.json`.
- Follow existing style conventions; do not enforce new tools unless requested.

## 4) Conventions from Existing Markdown Docs
- Keep frontend UI clean, simple, and minimal.
- For frontend implementation tasks, use the `frontend-design` skill.
- For substantial UI changes, run a `web-design-reviewer` pass.
- For Clerk webhook work, use the `clerk-webhooks` skill.
- For discovering/installing additional skills, use the `find-skills` skill.
- For chat UI work, align with `reference_images/chat_interface.png`.
- Keep responsive behavior predictable on desktop and mobile.

## 5) Global Formatting Rules
From `.editorconfig`:
- Charset: UTF-8
- End of line: LF
- Insert final newline: true
- Trim trailing whitespace: true
- Default indent: 2 spaces
- Python indent: 4 spaces
- Makefile indent: tabs

Editing expectations:
- Keep diffs small and task-scoped.
- Match surrounding style before introducing new abstractions.
- Avoid unnecessary comments, banners, and boilerplate.

## 6) Backend Style Guidelines (Python/FastAPI)

### Imports
- Order imports as stdlib, third-party, local (`app...`), with blank lines between groups.
- Prefer explicit imports; avoid wildcard imports.
- Use `TYPE_CHECKING` for type-only relationship imports.

### Types and SQLAlchemy
- Add type hints for all new/changed code.
- Prefer modern unions (`X | None`).
- Use SQLAlchemy 2 typed patterns consistently:
  - `Mapped[...]`
  - `mapped_column(...)`
  - explicit `relationship(...)`
- Keep UUID usage consistent in Python models and Postgres schema.

### Naming
- Modules/functions/variables: `snake_case`
- Classes: `PascalCase`
- Enum members: `UPPER_CASE`
- Enum serialized values: lowercase strings (e.g. `queued`, `assistant`).

### API design and validation
- Keep route handlers thin and focused on HTTP concerns.
- Validate boundary schemas with FastAPI/Pydantic models.
- Move non-trivial business logic into service modules.
- Keep response contracts explicit via response models where possible.

### Error handling and logging
- Raise clear HTTP errors with appropriate status codes.
- Do not swallow exceptions silently.
- Preserve request tracing behavior (`x-request-id` middleware/headers).
- Never log secrets, tokens, credentials, or sensitive payload content.

### Database and migrations
- Generate an Alembic migration for schema/model changes.
- Review generated migration scripts before applying.
- Keep migrations reversible where practical.
- Do not modify old migrations unless explicitly required.
- Preserve naming conventions defined in `backend/app/db/base.py`.

## 7) Frontend Style Guidelines (TypeScript/React)

### TypeScript and imports
- Keep strict TypeScript passing.
- Use explicit exported types for shared APIs, hooks, and data contracts.
- Use alias imports (`@/*`) when they improve readability.
- Keep import groups stable and readable.

### React and state
- Use functional components.
- Keep components focused and composable.
- Use TanStack Query for server state.
- Use Zustand for UI/local global client state.

### Naming
- Components/types: `PascalCase`
- Variables/functions/hooks: `camelCase`
- Hooks start with `use`.

### API, streaming, styling
- Centralize HTTP access in `frontend/src/lib/api/client.ts`.
- Always check `response.ok` and throw meaningful errors.
- Preserve SSE compatibility and cancellation support for streaming features.
- Reuse design tokens/conventions from `frontend/src/styles.css`.

## 8) Verification Expectations Before Handoff
- Backend code changes: run `make test`.
- Frontend code changes: run `cd frontend && pnpm build`.
- Schema changes: run `make db-upgrade` and relevant tests.
- If checks cannot run due to missing environment/tools, report that explicitly.

## 9) Agent Workflow Norms
- Read relevant files before editing.
- Avoid broad refactors outside requested scope.
- Prefer minimal, reversible diffs.
- Update this file when command workflows or style rules change.
