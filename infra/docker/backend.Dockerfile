FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

RUN pip install --no-cache-dir uv

COPY backend/pyproject.toml /app/pyproject.toml
RUN uv pip install --system -e ".[dev]"

COPY backend /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
