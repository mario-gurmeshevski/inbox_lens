# Testing

The project includes 944 tests covering all modules. Tests use temporary databases and mock external services (no IMAP credentials needed).

```bash

make test # For Mac/Linux
./commands.ps1 test # For Windows

make test-cov   # For Mac/Linux
./commands.ps1 test-cov # For Windows

```

## Test structure

| File                   | Tests | Coverage                                                                                                               |
| ---------------------- | ----- | ---------------------------------------------------------------------------------------------------------------------- |
| `test_web.py`          | 245   | FastAPI endpoints, SSE, Tailscale, auth middleware, update banner/panel, compose/drafts/signature, rate-limit cooldown |
| `test_imap.py`         | 172   | IMAP helpers, connection, fetch, delete, archive/move, flag sync, bulk ops, folder-aware selection                     |
| `test_cache.py`        | 153   | DB ops, hashing, scanning, search, threads, sent reconcile, folder migration                                           |
| `test_updater.py`      | 84    | Version parsing/checking, GitHub fetch cache + rate-limit, Docker self-update, swap-helper rollback                    |
| `test_email_reader.py` | 83    | Parsing, body cleaning, thread extraction, keywords                                                                    |
| `test_idle_monitor.py` | 67    | IDLE loop, ConnectionLost, run_initial_fetch                                                                           |
| `test_auth.py`         | 45    | Password hashing, API keys, sessions, rate limiting                                                                    |
| `test_smtp.py`         | 39    | SMTP session, message building (CC/BCC, multipart), reply/forward, persistence, rate limiting                          |
| `test_crypto.py`       | 22    | Encryption, settings, credentials                                                                                      |
| `test_drafts.py`       | 7     | Draft save/get/list/delete/upsert, stale pruning                                                                       |
| `test_event_bus.py`    | 12    | Pub/sub dispatch                                                                                                       |
| `test_utils.py`        | 8     | Keyword parsing, priority buckets                                                                                      |
| `test_constants.py`    | 7     | Env var defaults and overrides                                                                                         |

## Linting

```bash

make lint # For Mac/Linux

./commands.ps1 lint # For Windows

```
