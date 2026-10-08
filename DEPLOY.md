# Free backend deployment

This guide deploys the API to Koyeb, PostgreSQL to Neon, and Redis to Upstash.
The Docker image applies Alembic migrations and runs the idempotent bootstrap command before starting the API.

## 1. Push the repository to GitHub

Create an empty private repository on GitHub. From this repository's root run:

```bash
git add .
git commit -m "backend: prepare cloud deployment"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/project_oraz.git
git push -u origin main
```

Do not commit `.env`; it is already ignored.

## 2. Create PostgreSQL in Neon

1. Sign in at <https://console.neon.tech> and create a project.
2. Open **Connect**, select the database and copy the connection string.
3. Change only the URL scheme from `postgresql://` to `postgresql+asyncpg://`.
4. Keep all query parameters, including `sslmode=require` if Neon supplied it.

The resulting value will be used as `M1917_DATABASE_URL`.

## 3. Create Redis in Upstash

1. Sign in at <https://console.upstash.com> and create a Redis database.
2. Choose the region closest to the Koyeb service (Frankfurt is preferred).
3. Open the database details and copy the TLS Redis URL beginning with `rediss://`.

The value will be used as `M1917_REDIS_URL`. Do not use the REST URL or REST token.

## 4. Generate secrets locally

Run these commands and save each output separately:

```bash
openssl rand -hex 32
openssl rand -hex 32
cd backend && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

They are, in order, `M1917_JWT_SECRET`, `M1917_OTP_PEPPER`, and
`M1917_PII_ENCRYPTION_KEY`. Never put them in GitHub files.

## 5. Create the Koyeb service

1. Sign in at <https://app.koyeb.com> and choose **Create Web Service**.
2. Select **GitHub**, authorize the private repository, and select branch `main`.
3. Choose **Dockerfile** as the builder.
4. Set the work directory/build context to `backend` and the Dockerfile to `Dockerfile`.
5. Choose the free instance in Frankfurt.
6. Set the exposed port to `8000` and protocol to HTTP.
7. Set the health check path to `/health/live`.
8. Add the environment variables below, marking secrets as secret values.

| Variable | Value |
| --- | --- |
| `M1917_ENVIRONMENT` | `development` for a private demo; see production note below |
| `M1917_DATABASE_URL` | Neon URL with `postgresql+asyncpg://` |
| `M1917_REDIS_URL` | Upstash TLS URL beginning with `rediss://` |
| `M1917_JWT_SECRET` | first generated secret |
| `M1917_OTP_PEPPER` | second generated secret |
| `M1917_PII_ENCRYPTION_KEY` | generated Fernet key |
| `M1917_OTP_PROVIDER` | `console` for a private demo |
| `M1917_CORS_ORIGINS` | Flutter Web origin, for example `https://app.example.com` |
| `M1917_BOOTSTRAP_USERS_JSON` | initial users JSON described below |

For an initial test account, set `M1917_BOOTSTRAP_USERS_JSON` to a single-line JSON value:

```json
[{"phone":"+77000000001","role":"BOSS","full_name":"Owner","employee_id":"BOSS-001","city":"Almaty"}]
```

Deploy the service. The first startup can take longer because migrations are applied.

## 6. Verify the deployment

Replace `YOUR_APP` with the Koyeb domain:

```bash
curl https://YOUR_APP.koyeb.app/health/live
curl https://YOUR_APP.koyeb.app/health/ready
```

Both requests should return an `ok`/`ready` status. API documentation is at:

```text
https://YOUR_APP.koyeb.app/api/v1/docs
```

With the demo `console` OTP provider, request an OTP in the app and read its value in
the Koyeb runtime logs. This mode is not suitable for public production use.

## Production note

`M1917_ENVIRONMENT=production` intentionally refuses to start unless a real HTTPS SMS
gateway is configured with `M1917_OTP_PROVIDER=sms`, `M1917_SMS_GATEWAY_URL`, and
`M1917_SMS_API_TOKEN`. Switch to production only after configuring that provider.

The free Koyeb instance sleeps after inactivity, so the first request may be slow.
Neon and Upstash can also suspend or limit inactive/free resources. Use paid plans for
a service that requires production availability.
