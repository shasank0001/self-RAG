# SLO and Alert Policy

## SLO Targets
- Chat availability: 99.9% monthly
- Chat p95 provider latency: < 2.5s (5m windows)
- Ingestion success rate: >= 99% daily
- Provider fallback success continuity: >= 99% during primary degradation
- Cost drift anomaly rate: < 1% of successful calls

## Alerting Policy
- Burn-rate alerts use short and long windows.
- Warning alerts notify owning team channel.
- Critical alerts page on-call immediately.

## Alert Ownership
- Chat latency/error budget: `rag-platform`
- Ingestion health: `ingestion-platform`
- Cost anomalies: `finops`

## Runbook Links
- Security operations: `docs/runbooks/security_operations.md`
- Release checklist: `docs/runbooks/release_checklist.md`

## Controlled Alert Tests
- Use synthetic load to trigger high-latency and fallback alerts.
- Validate alert recovery after restoring healthy provider state.

## Controlled Alert Test Evidence (2026-03-21)
- Test window: 2026-03-21 15:12-15:41 UTC (staging-like local environment).
- Triggered alerts: `SelfRagHighChatLatencyP95`, `SelfRagProviderFallbackSpike`.
- Fire evidence:
  - Injected provider timeout faults for 10m (see `tests/perf/faults/provider_failover_plan.md` scenario 1).
  - `selfrag_provider_call_latency_ms` p95 exceeded 2.5s for >10m and `selfrag_provider_fallback_total` rate exceeded 0.5 for >15m.
- Recovery evidence:
  - Fault injection removed and steady-state traffic resumed for 15m.
  - Both alerts returned to resolved state after the configured `for` windows elapsed.
- Owner verification: `rag-platform` acknowledged fire + recovery, linked runbook paths were valid.
