# Authentication

The dashboard supports **password authentication** to protect it when exposed beyond localhost.

## How it works

- **Until a dashboard password is set**, the server binds to `127.0.0.1` only, even if `WEB_HOST=0.0.0.0` or Network Access is enabled. The dashboard is reachable only from the local machine.
- **On first run**, visiting the dashboard redirects to the **Dashboard Setup** page (`/setup-dashboard`) where you set a password. After setting it you're logged in and prompted to connect your email account.
- Once a password is set, the server may bind to `0.0.0.0` (subject to the Network Access toggle) and every visitor must log in.
- Sessions are stored in a signed `HttpOnly` cookie (`SameSite=Lax`).

## Setting / changing the password

- **First run**: set it on the `/setup-dashboard` page (optional: also generate an API key).
- **Later**: change it from **Settings → Authentication → Change Password** (requires the current password).

## API key (programmatic access)

Generate or revoke an API key from **Settings → API Key**. It is shown **once** at creation time. Use it to access `/api/*` endpoints without a browser session:

```bash

curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:8000/api/events

```

The SSE endpoint (`/api/events`) also accepts the key as a query parameter (`?api_key=...`) since `EventSource` cannot set headers, note that query strings can appear in server logs.

## Removing the password

To disable authentication and return to localhost-only binding, clear the password hash from the database, then restart the server.

**Docker** (the container has no `sqlite3` CLI, so use Python, the DB lives at `/app/src/data/emails.db`):

```bash
docker exec web python3 -c "import sqlite3; sqlite3.connect('/app/src/data/emails.db').execute(\"DELETE FROM settings WHERE key='dashboard_password_hash'\")"
make restart # or: docker compose restart web
```

**Local install:**

```bash
sqlite3 src/data/emails.db "DELETE FROM settings WHERE key='dashboard_password_hash'"
```
