# ALASH | M-1917 — Architecture and Implementation Plan

> Status (2026-10-05): Phases 1 and 2 are implemented and verified. Authentication/RBAC, workers, the five configured cities, brigades, objects, assignment history, role-safe projections, profile change approval, Flutter worker/master screens, and Docker Compose are operational. Next: Phase 3 operations.

## 1. System Architecture

The system will start as a modular monolith: one deployable API with strict internal module boundaries. This is simpler to operate and keeps financial transactions, RBAC checks, and audit writes atomic.

```text
Flutter iOS/Android (WORKER, MASTER)   Next.js Web (ADMIN, BOSS)
                    \                 /
                     FastAPI /api/v1
                           |
        PostgreSQL ---- Redis ---- S3/MinIO
                           |
                  background worker
                  SMS, FCM/APNs, WebSocket
```

Backend bounded contexts: `identity`, `workforce`, `organization`, `operations`, `finance`, `equipment`, `access_pass`, `rating`, `communications`, `audit`, and `reporting`. Each context exposes services and DTOs; routers must not query another context's tables directly.

## 2. Database Structure

PostgreSQL is the source of truth. UUID primary keys, UTC `TIMESTAMPTZ`, explicit enums/checks, foreign keys, indexes, optimistic version columns, and `archived_at` are used consistently.

- Identity: `users`, `employee_profiles`, `otp_challenges`, `auth_sessions`, `audit_logs`.
- Organization: `cities`, `brigades`, `objects`, `worker_assignments` with effective date history.
- Operations: `schedules`, `attendance`, `work_days`, `tariff_history`. Each work day stores its rate snapshot so old earnings never recalculate.
- Finance: `expenses`, `revenues`, `payroll_periods`, `financial_requests`, `ledger_entries`, worker/company bank accounts.
- Equipment/access: `equipment_items`, `stock_movements`, `equipment_issues`, `work_authorizations`, `digital_passes`, `pass_scans`.
- Rating/comms: immutable `rating_events`, announcements, notifications/deliveries, chat threads/messages, support/emergency events.
- Files: `file_objects` plus typed attachment links; binary data stays in S3/MinIO.

Money always uses `NUMERIC` plus ISO currency. Audit snapshots are redacted so OTPs, token hashes, passport values, and full IBANs never enter JSON logs.

## 3. Backend Structure

```text
backend/src/backend/
  main.py                 # app factory and lifecycle
  core/                   # config, DB, Redis, security, errors
  identity/               # users, OTP, sessions, auth routes
  authorization/          # permissions, role map, scope policies
  workforce/ organization/ operations/ finance/
  equipment/ access_pass/ rating/ communications/
  audit/ reporting/
backend/alembic/           # ordered PostgreSQL migrations
backend/tests/{unit,integration}/
```

Routers call application services. Services own transaction boundaries and use repositories. Pydantic request/response models are separate from ORM models. API errors use one machine-readable envelope and distinguish `401` from `403`.

## 4. Mobile App Structure

Flutter serves WORKER and MASTER only. `Riverpod` provides state/DI, `GoRouter` applies authenticated role-aware navigation, `Dio` handles API calls and token refresh, and `flutter_secure_storage` stores refresh credentials.

```text
mobile/lib/
  app/                    # bootstrap, routing, theme, localization
  core/                   # API client, secure storage, shared widgets
  features/auth/
  features/worker/
  features/master/
  features/chat/ notifications/
```

Kazakh is primary and Russian is fallback. UI route guards improve UX only; the backend remains the security boundary.

## 5. Admin/Boss Panel

A new `web/` Next.js TypeScript application will contain shared shell/design-system code and separate feature routes for ADMIN and BOSS. TanStack Query handles server state; React Hook Form validates forms. Server responses are role-specific projections: ADMIN finance payloads cannot contain revenue, net/object profit, profit margin, or Boss/company bank fields. BOSS screens receive full finance projections but do not inherit unspecified ADMIN mutations.

## 6. API Structure

All endpoints use `/api/v1`, OpenAPI schemas, pagination/filter conventions, idempotency keys for financial writes, and correlation IDs.

- Identity: `POST /auth/request-otp`, `/auth/verify-otp`, `/auth/refresh`, `/auth/logout`; `GET /me`, `GET/DELETE /me/sessions`.
- Worker: `/workers/me/*`, `/workers/public`, `/workers/{id}/public`.
- Master: `/master/team`, `/master/team/{id}/work-days`, `/master/brigade`, `/master/objects`.
- Admin: operational CRUD, expenses/reports, payroll, and financial requests.
- Boss: dashboard, revenues, expenses, finance/profit, bank accounts, audit, approvals.

Future resource endpoints follow the same permission + resource scope + response projection model rather than trusting URL prefixes alone.

## 7. Security and Exact RBAC

Authorization is deny-by-default and has three mandatory layers:

1. **Permission** — whether the role may perform the operation.
2. **Scope** — `own`, `direct_reports`, or `all`; MASTER queries always filter by `master_id`.
3. **Projection** — only role-safe fields are selected and serialized.

| Area | WORKER | MASTER | ADMIN | BOSS |
|---|---|---|---|---|
| Own data | full own view | own view | own view | own view |
| Other workers | sanitized public | assigned team, non-financial | operational access | view |
| Hours/m³ | own read | team read-only | CRUD | read |
| Worker bank | own | deny | read | read |
| Expenses | deny | deny | CRUD/reports | CRUD/reports* |
| Revenue/profit | deny | deny | explicit deny | full |
| Boss/company bank | deny | deny | explicit deny | full |
| Financial changes | deny | deny | create pending request | approve/reject |
| Audit | limited own | deny | admin scope | all |

`*` The explicit role matrix grants BOSS expense CRUD even though `API.md` omits a Boss expense route; dedicated Boss endpoints will enforce that stated right. Other safe resolutions: public bonus history excludes amounts; public pass excludes QR and sensitive permissions; MASTER phone is hidden until a precise need policy exists; only bootstrap/BOSS provisioning can assign roles; approved financial requests are never edited in place.

OTP is issued only for pre-provisioned active normalized E.164 phones. Codes are random, hashed, short-lived, single-use, attempt-limited, and rate-limited by phone and IP in Redis. The client cannot submit a role. Access JWTs are short-lived; opaque refresh tokens are hashed in PostgreSQL, rotated on every use, reuse-detected, device-scoped, and revocable. Secrets come from validated environment settings. Sensitive values are encrypted at application/KMS level, masked in DTOs, and excluded from logs.

## 8. Technology Choices

- Python 3.12, FastAPI, SQLAlchemy 2, Pydantic 2, Alembic, psycopg 3
- PostgreSQL, Redis, S3/MinIO, background worker with transactional outbox
- Flutter, Riverpod, GoRouter, Dio, Freezed/json_serializable
- Next.js, TypeScript, TanStack Query, React Hook Form, Playwright
- pytest, pytest-asyncio, HTTPX, Testcontainers/PostgreSQL
- Docker Compose locally; reverse proxy, structured logging, OpenTelemetry/Sentry, encrypted backups in production

## Phase 1 — Authentication + Database + 4-Role RBAC

### Implementation sequence

1. Restructure the FastAPI package with app factory, typed settings, async DB/Redis lifecycle, error envelope, request IDs, and `/health/live` + `/health/ready`.
2. Add PostgreSQL and Redis to `compose.yaml`, `.env.example`, persistent volumes, health checks, and migration startup instructions.
3. Configure Alembic and create the initial migration for role/account enums, `users`, minimal `employee_profiles`, `otp_challenges`, `auth_sessions`, and append-only `audit_logs` with required indexes/constraints.
4. Add a repeatable bootstrap command that reads four development users from environment/seed config. Role is never accepted by public auth DTOs.
5. Implement pluggable `OtpSender`: console/fake provider is allowed only in test/development; production startup fails without an SMS provider configuration.
6. Implement OTP request/verification, access JWT creation, refresh rotation/reuse revocation, logout, session listing/revocation, and `/me`.
7. Implement explicit `Permission` constants, immutable role-to-permission mapping, FastAPI guards, resource scope helpers, and separate own/public/admin response schemas.
8. Audit OTP/security events without secrets, login/logout/session revocation, denied sensitive access, and all user role/status changes.
9. Add unit and PostgreSQL integration tests, then update Docker and contributor commands.

### Critical files

- Modify: `backend/pyproject.toml`, `backend/Dockerfile`, `compose.yaml`, `AGENTS.md`.
- Replace the current bootstrap in `backend/src/backend/__init__.py`; add `main.py`, `core/*`, `identity/*`, `authorization/*`, `audit/*`.
- Add: `backend/alembic.ini`, `backend/alembic/**`, `backend/tests/**`, `.env.example`.
- Phase 1 mobile integration: replace the counter shell with `features/auth`, secure token storage, `/me` bootstrap, and four-role destination routing; WORKER/MASTER receive functional shells, ADMIN/BOSS are directed to the web-panel message until `web/` is implemented.

### Verification and completion gate

- `docker compose up --build` starts PostgreSQL, Redis, API, and Flutter Web; readiness waits for dependencies.
- `uv run alembic upgrade head` succeeds on an empty database and is repeatable in CI.
- `uv run pytest` covers OTP expiry/single-use/attempt/rate limits, unauthorized and inactive phones, refresh rotation/reuse, logout revocation, and role omission from auth requests.
- A permission matrix test iterates every role/permission pair. Dedicated tests prove MASTER cannot escape direct-report scope, ADMIN receives `403` for revenue/profit/Boss bank access, WORKER cannot access another worker's private fields, and BOSS-only actions reject all other roles.
- `flutter analyze` and `flutter test` pass for login validation, token bootstrap, logout, and role routing.
- OpenAPI exposes only intended schemas; automated response tests assert forbidden sensitive keys are absent, not merely `null`.
- No Phase 2 work starts until migration, auth, RBAC, security, and integration tests all pass.

## Later Delivery Stages

2. **Complete:** Workforce, cities, brigades, objects, assignment history, encrypted private data, public/private/team projections, and profile change approval.
3. **Implemented:** Schedule/attendance/work-day CRUD, historical tariff snapshots, worker/master shift views, and date-selectable calendars. Assignment validation uses effective-date history and city timezone. Tests cover closed assignments, dates outside assignment periods, and loss of master access after transfer. Payroll posting and Boss approval remain in Phase 5.
4. **Implemented:** Equipment inventory, atomic stock adjustments, issue/return history, worker own-equipment screen, expense CRUD and daily/monthly/object totals split by currency. Admin manages equipment; Boss has equipment read and expense CRUD as explicitly specified. Payroll and financial approval are separate Phase 5 workflows. Admin/Boss UI remains scheduled for Phase 9.
5. **Backend implemented:** Monthly salary calculation from approved work-day snapshots; idempotent pending financial requests; Boss approval/rejection; pending cancellation; append-only PostgreSQL payroll ledger; worker-owned finance screen. Duplicate salary posting is constrained per worker/month/currency. Boss corrections append revisioned old/new amounts with reason, idempotency and stale-value checks; originals remain immutable. Full web approval UI remains scheduled for Phase 9.
6. **Backend implemented:** Boss-only revenue CRUD/confirmation, encrypted company-bank CRUD with IBAN checksum validation, expense confirmation, and approved revenue-minus-expense totals by dates/city/object and currency. Editing an approved expense/revenue returns it to DRAFT. Existing expenses migrate to DRAFT, remain visible in Admin reports, and require Boss confirmation before profit inclusion. Admin/Boss screens remain in Phase 9.
7. **Initial implementation:** Mandatory dated work authorization, revocation, short-lived single-use QR pass, scan-time status validation, and worker pass/rating screens. Rating uses immutable violation/absence events and attendance-triggered 14-day rewards. Existing absences are backfilled. Authorization taxonomy and Admin scanner UI remain to be specified/implemented; current policy requires one generic work authorization.
8. **In progress:** Announcements with recipient-scoped in-app notifications; general and participant-only direct chats with idempotent message submission; worker-to-assigned-master chat; own support tickets and Admin/Boss resolution API. Flutter worker/master screens display notifications, chats, support form/history, and confirmation-gated 112 phone-handler launch with copy fallback. Migration `20261005_0010` creates communication tables and the general room; `0011` adds requester-scoped support retry keys. Push delivery, realtime transport, full list pagination, and native-device emergency-flow acceptance remain outstanding; this stage is not complete.
   Chat follow-up: foreground/visible-route polling every 10 seconds, paused while loading or backgrounded; cursor-based older history with thread-scoped validation and ID deduplication. This is not WebSocket realtime or push delivery.
   Push foundation: own encrypted/session-bound device registry and transactional per-notification outbox (`20261006_0012`); no historical backfill, external sender, mobile SDK integration or confirmed delivery yet. These remain acceptance gates before Phase 8 completion.
   Dispatch follow-up: provider-independent single-job processor with PostgreSQL claim locks, per-device submission records (`20261006_0013`), eligibility rechecks, expiry, bounded calls, capped retries, invalid-token revocation and secret-safe errors. External adapter, daemon/Compose wiring, live device acceptance, client deduplication and PostgreSQL concurrency tests are still required.
9. ADMIN/BOSS dashboards, reporting, files, and exports.
10. Full acceptance suite, observability, backup/restore drill, security review, and mobile/web deployment.
