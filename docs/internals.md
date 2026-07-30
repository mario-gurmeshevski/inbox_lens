# Internals

## Workflow

Use the **web dashboard** (`make web`) to fetch, scan, and manage emails.

## Performance

By default, email fetching uses **8 parallel IMAP connections** (`MAX_WORKERS = 8`, hardcoded in `src/scripts/email_reader/imap.py` — not an env var). Each worker opens its own connection and fetches a slice of the email IDs, significantly reducing total fetch time.

- Keyword scanning runs in a separate pool of up to 4 workers (`src/scripts/cache/scanner.py`)

- All emails are stored in a SQLite database with WAL mode for fast concurrent reads

- HTML-only emails are automatically converted to clean plain text for keyword scanning

## Background workers

Three daemon threads run alongside the FastAPI event loop (started in `lifespan`):

- **`IdleMonitor`** (`src/scripts/idle_monitor.py`) — holds an IMAP IDLE connection on INBOX and triggers a full `run_initial_fetch` on new-mail pushes (with a 30-min poll fallback).

- **`OutboxWorker`** (`src/scripts/outbox_worker.py`) — polls the `outbox` table every 30s for due rows and retries failed sends via `email_reader.send_message`, with backoff (1m, 5m, 15m, 1h, 4h) up to 6 attempts before marking a row terminal. Both workers notify the UI by publishing `refresh` events on the in-process event bus (`src/scripts/event_bus.py`), which is thread-safe via `asyncio.Queue.put_nowait`.

- **`UpdateChecker`** (Docker only) — checks for app version updates.

Single-process deployment (one uvicorn worker): the outbox table's `status` transitions serialize sends, so no cross-process locking is required.

## Sent folder sync

`sync_sent_replies` (`src/scripts/email_reader/imap.py`) runs at the end of every `run_initial_fetch`. It selects the resolved Sent folder, fetches headers, and for each message: inserts new ones, refreshes `is_read`/`is_starred` flags for already-cached ones (mirroring the inbox flag refresh), marks them `is_sent`, and reconciles ghost rows. The Sent folder is resolved by the `\Sent` special-use attribute, falling back to common names (`Sent`, `Sent Items`, `Sent Messages`, …) before the Gmail default.

## Database

All emails are stored in a SQLite database (`emails.db` by default) with the following schema:

- **`emails`** — each email is stored with its full metadata, body, keyword matches, thread info, and status (`fetched`, `checked`, `headers_only`, or `fetched_no_body`). Keyword matches are stored as JSON for flexible querying; thread grouping via `thread_id` extracted from email headers; `is_read`/`is_starred`/`is_sent` flags track state. Indexes on status, category, thread, in-reply-to, and date ensure fast lookups

- **`settings`** — key/value runtime configuration (SMTP host/port/security overrides, email signature, page size, theme, sent-mail visibility, network-access mode, etc.)

- **`drafts`** — autosaved in-progress messages (recipients, subject, body, compose `mode`, and thread references) with an `updated_at` timestamp. Stale drafts older than 30 days are pruned on startup; an index on `updated_at` supports that sweep

- **`outbox`** — the failed-send retry queue driven by `OutboxWorker`. Each row holds the message (recipients, body, optional HTML part) plus `mode`, `original_message_id`, `thread_id`, and `draft_id` to rebuild the send context, a `status` of `pending` / `sending` / `failed`, an `attempts` counter, `last_error`, and `next_attempt_at` (stored in UTC). Indexes on `status` and `(status, next_attempt_at)` back the worker's due-row query

- WAL mode is enabled for safe concurrent access
