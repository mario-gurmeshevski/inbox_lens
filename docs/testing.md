# Testing

The project includes **1,228 tests** covering all modules. Tests use temporary databases and mock external services (no IMAP credentials needed).

```bash

make test # For Mac/Linux
./commands.ps1 test # For Windows

make test-cov   # For Mac/Linux
./commands.ps1 test-cov # For Windows

```

## Test structure

| File                          | Tests | Coverage                                                                                                                                         |
| ----------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_web.py`                 | 306   | FastAPI endpoints, SSE, Tailscale, auth middleware, update banner/panel, compose/drafts/signature, rate-limit cooldown, attachments pages & APIs |
| `test_imap.py`                | 192   | IMAP helpers, connection, fetch, delete, archive/move, flag sync, bulk ops, folder-aware selection, attachment fetch via BODYSTRUCTURE           |
| `test_cache.py`               | 153   | DB ops, hashing, scanning, search, threads, sent reconcile, folder migration                                                                     |
| `test_attachments_storage.py` | 152   | Attachment storage, validation, at-rest encryption, atomic persist, orphan pruning, search/filter/pagination, received attachments               |
| `test_updater.py`             | 84    | Version parsing/checking, GitHub fetch cache + rate-limit, Docker self-update, swap-helper rollback                                              |
| `test_email_reader.py`        | 83    | Parsing, body cleaning, thread extraction, keywords                                                                                              |
| `test_idle_monitor.py`        | 67    | IDLE loop, ConnectionLost, run_initial_fetch                                                                                                     |
| `test_smtp.py`                | 58    | SMTP session, message building (CC/BCC, multipart), reply/forward, persistence, rate limiting, attachment MIME parts                             |
| `test_auth.py`                | 45    | Password hashing, API keys, sessions, rate limiting                                                                                              |
| `test_outbox.py`              | 25    | Outbox queue, retry logic, status transitions                                                                                                    |
| `test_crypto.py`              | 22    | Encryption, settings, credentials                                                                                                                |
| `test_outbox_worker.py`       | 20    | Outbox worker, exponential backoff, max attempts                                                                                                 |
| `test_event_bus.py`           | 12    | Pub/sub dispatch                                                                                                                                 |
| `test_utils.py`               | 8     | Keyword parsing, priority buckets                                                                                                                |
| `test_constants.py`           | 7     | Env var defaults and overrides                                                                                                                   |
| `test_drafts.py`              | 7     | Draft save/get/list/delete/upsert, stale pruning                                                                                                 |

## Linting

```bash

make lint # For Mac/Linux

./commands.ps1 lint # For Windows

```
