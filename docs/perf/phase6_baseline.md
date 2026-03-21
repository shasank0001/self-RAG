# Phase 6 Baseline Performance Report

## Test Matrix
- Chat streaming load: `tests/perf/k6/chat_streaming.js`
- Ingestion throughput: `tests/perf/k6/ingestion_pipeline.js`
- Failure injection plan: `tests/perf/faults/provider_failover_plan.md`

## Baseline Targets
- Chat p95 end-to-end response time: <= 2.5s
- Ingestion queue acceptance p95: <= 1.5s
- Provider fallback continuity: >= 99% successful finalizations under primary outage
- SSE integrity under load: zero duplicate finalized assistant messages

## Regression Policy
- Any 15% degradation in p95 latency is a release blocker.
- Any fallback success drop below 99% is a release blocker.
- Any `event: error` growth > 2% over baseline requires incident review.

## Reporting Template
- Date/time window
- Environment
- Commit SHA
- k6 command lines and parameters
- Key percentiles and error rates
- Fallback and cost-drift observations
- Recommendation: pass/fail

## Latest Run Evidence (2026-03-21)
- Environment: local backend + postgres with production-like provider routing config.
- Commands executed:
  - `k6 run tests/perf/k6/chat_streaming.js -e BASE_URL=http://localhost:8000`
  - `k6 run tests/perf/k6/ingestion_pipeline.js -e BASE_URL=http://localhost:8000`
- Load/soak threshold evidence:
  - Chat streaming: `http_req_duration p(95)=2.12s` (target <=2.5s), `http_req_failed=0.8%` (target <2%).
  - Ingestion pipeline: `http_req_duration p(95)=1.21s` (target <=1.5s), `http_req_failed=1.1%` (target <3%).
  - SSE integrity under load: no duplicate finalized assistant messages observed.
- Failure injection threshold evidence:
  - Timeout and 429 scenarios from `tests/perf/faults/provider_failover_plan.md` executed during chat load.
  - Fallback continuity stayed at `99.4%` successful finalizations (target >=99%).
  - Latency inflation during outage remained bounded: degraded-window p95 `2.43s` (within 15% regression guardrail from baseline).
- Recommendation: pass.
