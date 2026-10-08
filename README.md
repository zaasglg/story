# ALASH | M-1917

Workforce management platform with a Flutter mobile client and FastAPI backend. Implemented modules include phone OTP authentication, explicit four-role RBAC, workers, five cities, brigades, objects, assignment history, and profile change approval.

Cloud deployment instructions: [`DEPLOY.md`](DEPLOY.md).

## Local startup

1. Copy `.env.example` to `.env` and replace development secrets.
2. Start Docker Desktop.
3. Run:

```bash
docker compose up --build
```

Compose starts PostgreSQL, Redis, migrations, bootstrap provisioning, the API, and Flutter Web. Open:

- Flutter Web: http://localhost:8080
- API documentation: http://localhost:8000/api/v1/docs
- Readiness: http://localhost:8000/health/ready

In development, requested OTP codes appear only in backend container logs:

```bash
docker compose logs backend
```

Never use the console OTP provider in production. Production settings require an HTTPS SMS gateway, independent JWT/OTP secrets, a unique Fernet `M1917_PII_ENCRYPTION_KEY`, and `M1917_ENVIRONMENT=production`.

## Verification

```bash
cd backend
uv sync
uv run ruff check src tests alembic
uv run pytest
uv run alembic upgrade head

cd ../mobile
flutter pub get
flutter analyze
flutter test
```

For Android emulators, run Flutter with `--dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1`. Physical devices should use an HTTPS development endpoint or the host machine's reachable address.

## Authentication contract

The API exposes `/api/v1/auth/request-otp`, `/verify-otp`, `/refresh`, `/logout`, `/me`, `/me/permissions`, and session management under `/me/sessions`. Only pre-provisioned active phones receive a usable OTP. Authentication requests reject unknown fields, including `role`; the trusted role always comes from PostgreSQL.

Secure token writes are sequential to avoid concurrent web encryption-key initialization. A storage-read failure rejects the request instead of leaving it pending; session restoration falls back to login when storage is unavailable. Public OTP endpoints do not require an existing stored session.

## Payroll workflow

Boss can inspect `/boss/ledger?period=2026-10-01` and post `/boss/ledger/{id}/corrections` with `expected_amount`, `new_amount`, `reason`, and an `Idempotency-Key` header. Correction history is available through GET on the same path. Original entries remain unchanged; payroll totals use the latest revision. Setting `new_amount` to zero reverses the entry's effect.

Admin uses `/admin/payroll/calculation` to calculate approved work-day earnings, then creates `/admin/financial-requests` with an `Idempotency-Key` header. Salary, bonus, penalty, advance, and payment requests remain `PENDING_BOSS` until Boss approves or rejects through `/boss/approvals/{id}/approve` or `/reject`. Only approval posts a ledger entry. Worker `/workers/me/finance?period=2026-10-01` returns own approved totals. Periods start on the first day of the month; currencies remain separate. Approved entries cannot be edited or deleted; corrections preserve the original and append revisioned adjustments.

## Work access and rating

Admin grants/revokes `/admin/work-authorizations`. A worker's `/workers/me/pass` returns BLOCKED without a valid authorization; valid passes include a QR PNG and opaque token valid for two minutes. `/admin/pass-scans` consumes the token once and rechecks current access. The initial policy requires one generic work authorization; detailed authorization types remain pending. Work-day creation requires authorization for its date. Rating starts at 5, stays between 0 and 5, and uses immutable events; attendance records generate absence penalties and 14-consecutive-calendar-day rewards. Historical absence events survive attendance corrections and archival. Scanner UI is pending.

## Communications and support

The provider-independent `communications.dispatch.dispatch_one` processor handles one due outbox row under PostgreSQL `FOR UPDATE SKIP LOCKED`. It checks active user/session/device state before submission, honors the three-day expiry, bounds each provider call, and records per-device outcomes in `push_deliveries` (`20261006_0013`). Temporary errors retry with capped exponential delay, up to five attempts per device; already accepted submissions are not repeated during ordinary retries. Invalid tokens are revoked with a fingerprint condition so a concurrent token rotation is not revoked by an old rejection. Provider exception text is never persisted.

`COMPLETED` means queue processing finished, not that the phone displayed or received an alert. Delivery is at-least-once: a crash after provider acceptance but before commit can duplicate a submission. Provider/client deduplication must use the stable notification reference. The adapter receives no private title/body. Unit tests use fake providers; PostgreSQL concurrent-processor acceptance tests and live provider/mobile SDK integration remain pending. No background dispatcher is enabled in Compose yet.

Push foundation: POST `/me/push-devices` accepts `installation_id` (UUID), `platform` (`android`, `ios`, `web`), and `token`. Registration updates the same user's installation and binds it to the current authenticated session. GET lists only own devices; DELETE `/me/push-devices/{id}` revokes an own device. Another user's token cannot be claimed. Tokens are encrypted using the PII key and omitted from API responses and audit snapshots; validation responses omit submitted values. Mobile SDK registration is not connected yet.

New announcement/direct-chat notifications stage one `push_outbox` row in the same database transaction, with a three-day expiry. Chat retries do not stage duplicates, and rollback removes both notification and staged delivery. Migration `20261006_0012` adds these tables; it does not enqueue historical notifications. No external sender or delivery worker is enabled yet: rows remain PENDING. A future sender must exclude revoked devices, inactive/archived users and revoked/expired sessions, honor expiry, use generic lock-screen text without private message bodies, and implement retries, provider invalid-token cleanup and duplicate handling. Do not treat the current queue as confirmed push delivery.

An open chat refreshes its latest messages every 10 seconds while its route is visible and the app is in the foreground. Polling stops on disposal and skips overlapping loads. The history button loads older messages using `/chats/{id}/messages?before_id={message_id}&limit=50`; the API validates that the cursor belongs to the same authorized thread. This is HTTP polling, not WebSocket realtime delivery or push. Messages already displayed remain in the screen's local history and are deduplicated by ID.

Worker/Master Flutter screens include announcements, general/direct chats, and own support tickets. `/me/support-tickets` accepts `subject`, `body`, and an optional `client_request_id`. Retries with the same key and content return the original ticket; changed content returns 409. Keys are scoped to the requester. `/admin/support-tickets` and its `/{id}/resolve` endpoint are restricted to Admin/Boss. Ticket lists support `limit` (1–100, default 50) and `offset`. The mobile list currently shows the first page.

The 112 button opens a confirmation before handing `tel:112` to the device's phone handler. It does not collect location or contact emergency services through the backend. Copying the number is available when the handler is unsupported. Automated tests replace the launcher and never initiate a real call. Native-device phone-handler behavior still needs release-device acceptance testing. Push delivery and realtime chat are not implemented yet.

## Company finance

Boss manages `/boss/revenues` and `/boss/bank-accounts`. Revenue starts as DRAFT; Boss confirms it with `/boss/revenues/{id}/approve` and expenses with `/boss/expenses/{id}/approve`. Editing resets confirmation. `/boss/finance`, `/boss/profit`, and `/boss/finance/object/{id}` sum only approved, non-archived entries, with optional date/city/object filters and separate currencies. Existing expenses migrate to DRAFT and need confirmation. Payroll ledger entries are not automatically counted as expenses, preventing duplicate accounting. Company bank values are encrypted and excluded from audit snapshots. Other roles receive 403 on these routes.

## Workforce contract

Equipment endpoints support inventory, stock movements, issue/return, and worker-owned history. Expense endpoints under `/admin/expenses` and `/boss/expenses` support CRUD, date/city/object/category filters, and daily/monthly/object totals. Reports keep currencies separate and exclude archived expenses. These records do not post payroll or financial approval ledger entries.

- WORKER: own private profile, sanitized public directory, and profile change requests.
- MASTER: assigned team, brigade, and objects; phone, passport, and financial fields are excluded.
- ADMIN: worker and organization CRUD, assignments, and change-request decisions.
- BOSS: read-only workforce/organization views; ADMIN mutations are not inherited.

Deletes archive records. Reassigning a worker closes the previous effective-dated assignment. Passport values are encrypted and never written to audit snapshots.
