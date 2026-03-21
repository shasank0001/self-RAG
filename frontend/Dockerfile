FROM node:20-bookworm-slim

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN corepack enable && (pnpm install --frozen-lockfile || pnpm install)

COPY frontend .
RUN pnpm build

USER app
EXPOSE 4173

CMD ["pnpm", "preview", "--host", "0.0.0.0", "--port", "4173"]
