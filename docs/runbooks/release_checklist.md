# Release Checklist

## Pre-release
- CI pipeline green (`lint`, `type`, `test`, `migration`, `security`).
- Alerting and dashboards healthy in staging.
- Backup and restore drill completed within target RPO/RTO.
- Rollback plan documented for this release.
- Backup/restore drill evidence attached: `docs/runbooks/backup_restore_drill.md`.

## Deploy
- Promote build to staging and run smoke tests.
- Verify traces include API -> graph -> retrieval -> provider path.
- Verify metrics ingestion and `/metrics` endpoint health.

## Post-deploy
- Confirm chat p95 latency and error rates are within SLO.
- Confirm fallback rate is within baseline.
- Confirm ingestion throughput and failure rates are stable.
- Confirm no critical security findings.

## Rollback Trigger Conditions
- Smoke checks fail post-deploy.
- Error budget burn exceeds critical threshold.
- Security gate or runtime critical vulnerability detected.
