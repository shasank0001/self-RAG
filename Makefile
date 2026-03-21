.PHONY: setup setup-backend setup-frontend dev-backend dev-frontend test db-revision db-upgrade db-downgrade

setup: setup-backend setup-frontend

setup-backend:
	cd backend && if command -v uv >/dev/null 2>&1; then uv venv && uv sync; else python3 -m pip install -e ".[dev]"; fi

setup-frontend:
	cd frontend && if command -v pnpm >/dev/null 2>&1; then pnpm install; else npm install; fi

dev-backend:
	cd backend && if command -v uv >/dev/null 2>&1; then uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000; else python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000; fi

dev-frontend:
	cd frontend && if command -v pnpm >/dev/null 2>&1; then pnpm dev; else npm run dev; fi

db-revision:
	cd backend && if command -v uv >/dev/null 2>&1; then uv run alembic revision --autogenerate -m "$(m)"; else python3 -m alembic revision --autogenerate -m "$(m)"; fi

db-upgrade:
	cd backend && if command -v uv >/dev/null 2>&1; then uv run alembic upgrade head; else python3 -m alembic upgrade head; fi

db-downgrade:
	cd backend && if command -v uv >/dev/null 2>&1; then uv run alembic downgrade -1; else python3 -m alembic downgrade -1; fi

test:
	cd backend && if command -v uv >/dev/null 2>&1; then uv run pytest; else python3 -m pytest; fi
