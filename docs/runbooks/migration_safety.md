# Migration Safety and Rollback Playbook

## Strategy
- Use expand/contract migrations.
- Avoid destructive schema changes in the same release as data backfills.
- Always test upgrade and downgrade on staging snapshots.

## Pre-deploy Checklist
- Verify migration scripts are reversible.
- Confirm backup is recent and restore-tested.
- Confirm CI migration check passed.

## Rollback Procedure
1. Stop write traffic.
2. Run `alembic downgrade -1` when safe and reversible.
3. If rollback is unsafe, restore DB from latest validated backup.
4. Re-deploy previous stable application image.
5. Run smoke checks and monitor error rates.

## Post-rollback Validation
- API health and readiness pass.
- Core chat + ingestion workflows pass smoke checks.
- No persistent migration lock or schema drift.
