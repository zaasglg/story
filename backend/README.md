# ALASH M-1917 API

FastAPI modular backend for the ALASH M-1917 workforce platform. The identity foundation uses PostgreSQL, Redis rate limiting, hashed single-use OTP challenges, short-lived JWT access tokens, rotating opaque refresh tokens, and explicit server-side role permissions.

Use the repository root `README.md` for startup and verification commands. Configuration is loaded from `M1917_*` environment variables; public authentication payloads never accept a role.
