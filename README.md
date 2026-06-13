# Inbox Lens

Fetch emails from a Gmail inbox via IMAP, cache them in a local SQLite database, and scan with keyword-based priority tagging. Includes a web dashboard and an optional Telegram bot for 24/7 monitoring with proactive alerts.

## Prerequisites

- Python 3.12+

- A Gmail account with 2-Step Verification enabled and an [App Password](https://myaccount.google.com/apppasswords) generated

## Quick Start

1. **Run the web dashboard** (Docker with auto-restart & persistent storage):

   ```bash

   make up

   ```

   Open `http://localhost:8000` — on first visit you'll be prompted to connect your email account. You'll need a Gmail [App Password](https://myaccount.google.com/apppasswords) (2-Step Verification must be enabled).

2. **Define your keywords** (optional):

   On first Docker boot, `keywords.json` is **auto-created** from `keywords.example.json` in your mounted `./data/` directory with sensible defaults. Edit it to customize priority levels:

   ```json
   {
     "categories": {
       "10": ["urgent", "asap", "immediately", "action required"],

       "8": ["invoice", "payment", "refund", "charge"],

       "5": ["verify your account", "click here", "password expire"],

       "1": ["unsubscribe", "no-reply", "newsletter"]
     }
   }
   ```

   For **local dev**, copy the example first:

   ```bash

   cp src/data/keywords.example.json src/data/keywords.json

   ```

   Categories are **numeric priority levels from 1 to 10**, where **10 is highest priority** and **1 is lowest**. Each level contains words or phrases to match against email subjects and bodies.

   For running without Docker, the Telegram bot, and other options, see [Host Web](#host-web) and [Telegram Bot](#telegram-bot).

## Testing

The project includes 193 tests covering all modules. Tests use temporary databases and mock external services (no IMAP or Telegram credentials needed).

```bash

make test

# or: python3 -m pytest src/tests/ -v

```

### Test structure

| File | Tests | Coverage |
| ---------------------- | ----- | --------------------------------------------------------- |
| `test_cache.py` | 81 | Database operations, hashing, scanning, search, threads |
| `test_email_reader.py` | 60 | Email parsing, body cleaning, thread extraction, keywords |
| `test_bot.py` | 32 | Progress display, formatting, authorization, keyboards |
| `test_web.py` | 16 | FastAPI endpoints, progress callback, redirects |
| `test_constants.py` | 4 | Environment variable defaults and overrides |

### Linting

```bash

make lint

# or: python3 -m ruff check src/

```

## Host Web

The web dashboard is a FastAPI app that can be hosted via Docker or run directly on your machine.

### Docker (Recommended)

Runs in a container with automatic restarts and persistent storage.

```bash

docker compose up -d

# or: make up

```

The dashboard is available at `http://localhost:8000`. On first visit you'll be redirected to the setup page to connect your email account.

**Data persistence** — `docker-compose.yml` mounts `./data:/app/data` so the database, encryption key, and keywords survive container restarts and image updates.

**Updating keywords** — `keywords.json` is auto-created from `keywords.example.json` in `./data/` on first boot. Edit `./data/keywords.json` at any time — changes are picked up on the next scan (no rebuild needed).

To stop the container:

```bash

docker compose down

# or: make down

```

### Local

Install dependencies, then run the dashboard directly with uvicorn (auto-reloads on file changes):

```bash

make install      # or: pip install -r requirements.txt

make web

```

For development (includes test and lint tools):

```bash

make dev-install  # or: pip install -r requirements-dev.txt

```

Opens at `http://localhost:8000`. Set `WEB_HOST` and `WEB_PORT` in `.env` to customize.

### Features

- **Dashboard** — stats cards (total, headers only, checked, unscanned), priority distribution bar, recent emails

- **Live auto-refresh** — Server-Sent Events (SSE) updates the dashboard and email list in real-time as new emails arrive via IMAP IDLE — no page reload needed

- **Email list** — filterable by status, priority, and search text; paginated; responsive card layout on mobile

- **Email detail** — full body view, colored keyword tags, delete button

- **Settings page** — toggle network access (bind to `0.0.0.0` vs `127.0.0.1`), view local IPs and access URLs

- **Account page** — view connected email address, masked password, disconnect button

- **Responsive** — mobile-friendly with adaptive layouts, touch-optimized controls, and safe-area support for notched devices

### Limitations

- **Docker = web only** — the Telegram bot is not included in the Docker image. Run `make bot` separately if you need bot functionality.

- Set `IMAP_SERVER` in `.env` if you're not using Gmail (loaded automatically via Docker `env_file`).

## Configuration

### `.env` file

| Variable | Default | Description |
| -------------------- | ---------------- | ------------------------------------------------- |
| `IMAP_SERVER` | `imap.gmail.com` | IMAP server address |
| `DB_PATH` | `emails.db` | SQLite database path |
| `KEYWORDS_FILE` | `keywords.json` | Path to keywords config file (`/app/data/keywords.json` in Docker) |
| `SECRET_KEY_PATH` | `.secret.key` | Path to encryption key (auto-generated on login) |
| `WEB_HOST` | `0.0.0.0` | Web dashboard host |
| `WEB_PORT` | `8000` | Web dashboard port |
| `HOST_IP` | — | Host IP for network access display (auto-detected)|
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (for bot mode) |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID for proactive alerts |
| `ALLOWED_CHAT_IDS` | — | Comma-separated chat IDs allowed to use the bot |
| `ALERT_MIN_PRIORITY` | `7` | Minimum priority to trigger Telegram alerts |

Email credentials are configured at runtime via the web setup page or the Telegram `/login` command — not in `.env`.

### `keywords.json` file

Define priority levels as numeric keys (1-10), each containing a list of words or phrases to scan for. Emails are scanned against the subject and full body text.

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

When a keyword is found, the email is tagged with the matching priority level and the specific words matched. Tags are color-coded:

- **Red** — level 9-10 (critical)

- **Orange** — level 7-8 (high)

- **Yellow** — level 4-6 (medium)

- **Gray** — level 1-3 (low)

- **Light gray** — unclassified

## Workflow

Use the **web dashboard** (`make web`) or **Telegram bot** (`make bot`) to fetch, scan, and manage emails.

## Performance

By default, email fetching uses **8 parallel IMAP connections** (`MAX_WORKERS = 8`). Each worker opens its own connection and fetches a slice of the email IDs, significantly reducing total fetch time.

- All emails are stored in a SQLite database with WAL mode for fast concurrent reads

- HTML-only emails are automatically converted to clean plain text for keyword scanning

## Telegram Bot

The bot runs 24/7 on your local machine and provides an interactive Telegram interface with proactive alerts for high-priority emails.

### Setup

1. **Create a Telegram bot:**
   - Message [@BotFather](https://t.me/BotFather) on Telegram

   - Send `/newbot` and follow the prompts

   - Copy the bot token

2. **Get your Chat ID:**
   - Message [@userinfobot](https://t.me/userinfobot) on Telegram

   - Copy your Chat ID

3. **Configure `.env`:**

   ```

   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...

   TELEGRAM_CHAT_ID=123456789

   ALERT_MIN_PRIORITY=7

   ```

4. **Run the bot:**

   ```bash

    make bot

   ```

### Bot Commands

| Command | Description |
| -------------- | --------------------------------------- |
| `/start` | Show main menu with inline buttons |
| `/login` | Connect your email account |
| `/logout` | Disconnect your email account |
| `/help` | Show available commands |
| `/offline` | List cached emails (no IMAP) |
| `/offlinescan` | Scan cached emails offline |
| `/ordered` | Browse scanned emails by priority |
| `/delete` | Delete an email by Message-ID |
| `/status` | Show cache stats & monitor status |
| `/cancel` | Cancel current operation |

### Quick-Action Buttons

Every email card now includes inline action buttons:

- **📖 Full Body** — fetch and display the full email body

- **🗑 Delete** — delete with inline confirmation (no need to copy Message-IDs)

### Thread Grouping

Emails are automatically grouped into conversation threads based on `In-Reply-To`, `References`, and `Thread-Index` headers, with subject-based fallback grouping.

### IMAP IDLE Monitoring

Both the web dashboard and the Telegram bot use **IMAP IDLE** for real-time push notifications. Instead of polling on a timer, the connection stays open and the server pushes a notification the moment a new email arrives.

- Automatic reconnection with 30 s backoff on connection loss
- IDLE session renewed every 25 minutes (per IMAP RFC 2177 recommendations)
- Graceful fallback — if the server doesn't support IDLE, the monitor logs an error and stops
- On new mail: fetches headers + bodies, scans keywords, then fires callbacks (dashboard refresh or bot alerts)

### Proactive Alerts

When `TELEGRAM_CHAT_ID` is set, new emails detected via IMAP IDLE are scanned immediately. Emails with priority >= `ALERT_MIN_PRIORITY` (default 7) trigger a notification with action buttons:

```

🚨 1 high-priority email(s) detected!


🔴 [10] Urgent: Q4 report due today

From: boss@company.com

Date: Fri, 06 Jun 2026 09:00:00

Tags: 🔴10: urgent | 🟠8: payment


[📖 Full Body] [🗑 Delete]

```

Priority emojis: 🔴 9-10, 🟠 7-8, 🟡 4-6, 🔵 1-3, ⚪ unclassified

## Database

All emails are stored in a SQLite database (`emails.db` by default) with the following schema:

- Each email is stored with its full metadata, body, keyword matches, thread info, and status (`fetched`, `checked`, or `headers_only`)

- Keyword matches are stored as JSON for flexible querying

- Thread grouping via `thread_id` extracted from email headers

- Indexes on status, category, thread, and date ensure fast lookups

- WAL mode is enabled for safe concurrent access

## Make Targets

| Target | Description |
| ------------------ | ------------------------------------ |
| `make install` | Install Python dependencies |
| `make dev-install` | Install dev dependencies (test/lint) |
| `make web` | Run the web dashboard |
| `make bot` | Run the Telegram bot |
| `make up` | Build and start Docker container |
| `make down` | Stop and remove Docker container |
| `make test` | Run the test suite |
| `make lint` | Run the linter |
| `make clean` | Remove build artifacts |
| `make reset` | Delete DB, WAL files, and secret key |

## License

[MIT](LICENSE)
