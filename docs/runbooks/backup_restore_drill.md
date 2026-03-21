# Backup and Restore Drill — Phase 6

## Drill Metadata
- Date: 2026-03-21
- Environment: local docker postgres (`selfrag-postgres`)
- Backup command: `bash ops/db/backup.sh`
- Restore command: `bash ops/db/restore.sh ops/db/backups/selfrag_20260321_150133.dump`

## Results
- Backup output:
  - `Backup created: ops/db/backups/selfrag_20260321_150133.dump`
  - `Backup duration_seconds: 0`
- Restore output:
  - `Restore completed from: ops/db/backups/selfrag_20260321_150133.dump`
  - `Restore duration_seconds: 1`

## RPO/RTO Assessment
- RPO target: <= 1 day
- RTO target: <= 10 minutes
- Achieved:
  - RPO: met (fresh backup generated immediately prior to restore)
  - RTO: met (restore completed in 1 second)

## Notes
- Scripts support host `pg_dump`/`pg_restore` and docker fallback mode.
- Repeat this drill before production promotions involving schema changes.

## Restore Validation Evidence
- Post-restore checks completed successfully:
  - `GET /api/v1/health` returned `{"status":"ok"}`.
  - `GET /api/v1/ready` returned `{"status":"ready"}`.
  - Authenticated smoke flow for chat + ingestion remained operational after restore.
- Deploy gate linkage:
  - `docs/runbooks/release_checklist.md` references this drill as required pre-release evidence.
  - `.github/workflows/deploy.yml` enforces predeploy CI gates, staged-promotion evidence for production, post-deploy smoke checks, and explicit rollback execution path.
