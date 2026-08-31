# Stage 2.0 postpaid blocking incident — 2026-08-31

Operator: `515663171`.

Expected: payment due at 18:50 MSK, grace until 19:50 MSK, then active session must end with `payment_required`.

Observed: billing changed to `blocked` at 19:50, but the active lease continued to receive `allowed=True` heartbeats.

Root cause: an active unlimited `admin` grant remained from build 69 safety testing:
- grant: `wbuTzxJhH2Wbw4A-GlQzAQRV`
- source_id: `build-69-test-safety`
- grant kind: `unlimited_period`
- no expiry

This grant correctly overrides postpaid billing by entitlement design, but it was test contamination and should not have remained active for this production operator.

Production fix:
- pre-change DB backup: `/opt/masha-auth/backups/auth.db-before-revoke-build69-test-safety-20260831T195858`
- backup `PRAGMA integrity_check`: `ok`
- stale test grant revoked
- postpaid grant remains active

Verification:
- 19:59:02 heartbeat: `allowed=False`, `reason=payment_required`
- active lease `byFc_cpBJwzI8cBWW0qjhY_H` finished with `payment_required`
- 19:59:04 new authorize attempt denied with `payment_required`
- `GET /v1/access/status` now returns `allowed=false`, `billing_status=blocked`
