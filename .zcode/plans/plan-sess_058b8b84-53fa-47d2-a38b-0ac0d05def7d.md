## Doc audit result: docs are accurate, two surgical fixes needed

Audited all doc files against the actual code on `update-1.9.4`. The 1.9.4 feature set (outbox, SMTP settings UI, compose, drafts, signatures, 10/min rate limit, threads, Sent-folder sync) is accurately covered in README.md, features.md, configuration.md, and internals.md. commands.md matches the real Makefile/PowerShell targets (no phantom worker command). ROADMAP.md is clean (only genuinely-unshipped work). **No misleading content.** Two surgical edits only.

### Edit 1 — `docs/internals.md` (the one real staleness issue)
The "Database" section (lines 33–46) describes only the `emails` table + keyword/thread indexes, even though the *same file's* "Background workers" section already references the `outbox` table, and `db.py` defines `drafts` and `outbox` tables (confirmed at db.py:53–87). Add bullets covering:
- **`settings`** table — key/value runtime config (SMTP overrides, signature, page size, theme, etc.)
- **`drafts`** table — autosaved in-progress messages (id, recipients, subject, body, mode, thread refs, `updated_at`); pruned after 30 days on startup; index on `updated_at`
- **`outbox`** table — failed-send retry queue (recipients + body/HTML + `mode`/`original_message_id`/`thread_id`/`draft_id` + `status` `pending`/`sending`/`failed` + `attempts`, `last_error`, `next_attempt_at` in UTC); indexes on `status` and `(status, next_attempt_at)` for the worker's due-query

Keep prose-style (bulleted, no raw DDL) to match the existing section.

### Edit 2 — `docs/configuration.md` (optional polish, add missing operational numbers)
The "SMTP (outbound)" / Outbox section documents security modes and backoff in detail but omits two concrete, user-relevant values. Add a short "Limits" note after the Outbox subsection:
- **Send rate limit:** 10 sends/minute per account (confirmed `auth.py:150`, `RateLimiter(max_attempts=10, window_seconds=60)`)
- **Test-connection cooldown:** 20 seconds between tests (`SMTP_TEST_COOLDOWN = 20`, web.py:877); the test logs in only, it sends no probe email
- **Worker only runs when email credentials are configured** (web.py:99–101) — i.e. the outbox never starts before setup is complete

### Not touched (intentionally)
README.md, features.md, commands.md, ROADMAP.md, hosting.md — verified accurate, no edits.

### Verification
After edits: `grep -ni "drafts table\|outbox table\|20 sends\|10 sends\|cooldown"` across `docs/` to confirm the new content landed; re-read the two edited sections end-to-end.