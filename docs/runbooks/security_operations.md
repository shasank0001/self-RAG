# Security Operations Runbook

## Runtime Controls
- Request body limit enforced by `BodySizeLimitMiddleware`.
- Rate limiting enforced by `RateLimitMiddleware` on API and SSE routes.
- Payload guards enforce upper bounds for large text inputs.

## CI Security Gates
- Dependency scan (`pip-audit`, `npm audit`).
- Secret scan (`gitleaks`).
- Container scan (`trivy`).
- Policy checks (`conftest`) against OPA baseline.

## Incident Triage
1. Identify alert source and affected service.
2. Check recent deploys and workflow outputs.
3. Rotate credentials if secret leakage suspected.
4. Apply temporary rate-limit tightening if abuse suspected.
5. File incident report with mitigation and follow-up actions.

## Required Evidence
- Security workflow run URL
- Scan report artifacts
- Mitigation timeline and owner
