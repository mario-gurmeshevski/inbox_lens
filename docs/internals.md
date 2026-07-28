# Internals

## Workflow

Use the **web dashboard** (`make web`) to fetch, scan, and manage emails.

## Performance

By default, email fetching uses **8 parallel IMAP connections** (`MAX_WORKERS = 8`, hardcoded in `src/scripts/email_reader/imap.py` — not an env var). Each worker opens its own connection and fetches a slice of the email IDs, significantly reducing total fetch time.

- Keyword scanning runs in a separate pool of up to 4 workers (`src/scripts/cache/scanner.py`)

- All emails are stored in a SQLite database with WAL mode for fast concurrent reads

- HTML-only emails are automatically converted to clean plain text for keyword scanning

## Database

All emails are stored in a SQLite database (`emails.db` by default) with the following schema:

- Each email is stored with its full metadata, body, keyword matches, thread info, and status (`fetched`, `checked`, `headers_only`, or `fetched_no_body`)

- Keyword matches are stored as JSON for flexible querying

- Thread grouping via `thread_id` extracted from email headers

- Indexes on status, category, thread, and date ensure fast lookups

- WAL mode is enabled for safe concurrent access
