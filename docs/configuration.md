# Configuration

## `.env` file

| Variable                 | Default          | Description                                                                |
| ------------------------ | ---------------- | -------------------------------------------------------------------------- |
| `IMAP_SERVER`            | `imap.gmail.com` | IMAP server address                                                        |
| `SMTP_SERVER`            | `smtp.gmail.com` | SMTP server address (used for reply/forward; reuses IMAP credentials)      |
| `SMTP_PORT`              | `465`            | SMTP server port                                                           |
| `WEB_HOST`               | `0.0.0.0`        | Web dashboard host (forced to `127.0.0.1` if no dashboard password is set) |
| `WEB_PORT`               | `8000`           | Web dashboard port                                                         |
| `HOST_IP`                | /                | Host IP for network access display (auto-detected)                         |
| `SESSION_COOKIE_SECURE`  | `false`          | Set `true` to mark the session cookie `Secure` (use behind HTTPS)          |
| `SESSION_COOKIE_MAX_AGE` | `2592000`        | Session lifetime in seconds (default 30 days)                              |

The database (which holds keyword configuration), encryption key, and session secret live under `src/data/` (`/app/src/data/` in Docker) and are fixed to that location — they are not configurable via `.env`. Email credentials are configured at runtime via the web setup page — not in `.env`.

## Priority keywords

Define priority levels as numeric keys, each containing a list of words or phrases to scan for. Emails are scanned against the subject and full body text. Keywords are managed from the dashboard at **Keywords** (`/keywords`) and stored in the database; the JSON shape below is what **Import** / **Export** use:

```json
{
  "categories": {
    "10": ["urgent", "asap", "immediately"],

    "8": ["invoice", "payment", "refund"],

    "5": ["verify your account", "click here"],

    "1": ["unsubscribe", "no-reply", "newsletter"]
  }
}
```

Levels are positive integers (defaults 1–10, but arbitrary higher levels are allowed) — **higher numbers are more urgent**. On first run, the database is auto-filled from `keywords.json` if present (e.g. a config from a prior install), otherwise from the bundled `keywords.example.json`.

When a keyword is found, the email is tagged with the matching priority level and the specific words matched. Tags are color-coded:

- **Black** — level 9-10 (critical)

- **Red** — level 7-8 (high)

- **Orange** — level 4-6 (medium)

- **Yellow** — level 1-3 (low)

- **Light gray** — unclassified
