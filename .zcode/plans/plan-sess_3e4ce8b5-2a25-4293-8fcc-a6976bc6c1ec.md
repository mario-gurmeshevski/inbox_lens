# Fix Plan — `update-1.9.2` Review Issues

Fixing all 7 findings (F1–F7). Both open questions locked: limiter keys on plaintext `email_user` setting (verified in `crypto.py:33`); F6 fixed with lookahead + TDD (per your choice).

## Design decisions (locked)
- **Rate-limit response = `204 + X-Toast-Tone: warning`** (not 429). The frontend `compose.js`/`emails.js` already parse `X-Toast` headers; a 429 with no header would be silently swallowed. Zero frontend changes needed to surface the message.
- **Reuse the login limiter pattern** (`auth.py:115`) as a generic `RateLimiter`; `LoginRateLimiter` becomes a backward-compat alias so existing tests don't churn.
- **Send limiter key = `email_user` setting** (plaintext, decrypt-free — verified `crypto.py:33`), fallback to client IP if unconfigured.
- **All tests follow existing per-class `setup`/`_client`/`patch.object(web, "DB_PATH", …)` idioms** — no new fixtures.

## Phase 1 — Backend security & correctness

### 1.1 Generalize rate limiter — `src/scripts/auth.py` (F1)
- Rename `LoginRateLimiter` → `RateLimiter`; rename `ip` param → `key` in `_prune`/`is_limited`/`record_failure`/`reset` (pure rename, same logic). Keep `LoginRateLimiter = RateLimiter` alias + module singleton `login_rate_limiter`.
- Add module singleton: `send_rate_limiter = RateLimiter(max_attempts=10, window_seconds=60)`.

### 1.2 Enforce send rate limit — `src/scripts/web.py` (F1)
In `send_email` (`web.py:1384`):
- Remove the `# TODO(security)...` comment.
- **Before** field validation, add: `limiter_key = cache.get_setting("email_user", DB_PATH) or auth._client_ip(request)`; if `auth.send_rate_limiter.is_limited(limiter_key)` → `return _toast("You're sending too quickly. Please wait a minute and try again.", "warning")`. (Placed before validation so spam is throttled even on malformed payloads.)
- **Before** the `try:` wrapping `asyncio.to_thread(...)`, add `auth.send_rate_limiter.record_failure(limiter_key)` — counts every send attempt (success or SMTP failure), since outbound volume is what we cap.

### 1.3 Fix `decode_str` charset crash — `src/scripts/email_reader/parser.py:33` (F2)
Wrap the decode in `try/except LookupError:` falling back to `utf-8` (mirrors `get_text_body:136–141`). Note: `errors="replace"` doesn't catch `LookupError` (bad codec *name*), only decode errors on a known codec.

### 1.4 Fix forward recipient pre-fill — `web.py:1562` (F3)
`to_addr = "" if mode == "forward" else parseaddr(email_data.get("from") or "")[1]`. Forward leaves "To" blank; user picks a new recipient.

### 1.5 Tighten `strip_quoted_history` From:-heuristic — `parser.py` (F6, TDD)
- Add helper `_has_following_header(lines, start, window=3)` — True if a `Date:`/`Subject:` line appears within `window` lines after `start`.
- In the cut-condition (`parser.py:90–99`), replace `_RE_OUTLOOK_FROM.match(stripped)` with `_RE_OUTLOOK_FROM.match(stripped) and _has_following_header(lines, i)`. A bare prose `"From: the team"` (not followed by Date/Subject) no longer cuts; legitimate Outlook/Forwarded blocks (followed by Subject/Date) still cut.
- Write the two regression tests **first** (3.6) so the locale/Outlook canaries run before and after.

## Phase 2 — Frontend DRY (F5)

### 2.1 Add shared `window.postForm` — `src/web/js/base.js`
Union of the two duplicated blocks (handles arrays, returns `resp`, surfaces `X-Toast`/`X-Toast-Tone`). `base.html:143–144` loads `toast.js` then `base.js` on every page, so it's available to both `compose.js` and `emails.js`.

### 2.2 Refactor `compose.js:82–104` (F5)
Replace inline `fetch`+`.then` with `window.postForm(...)`. Keep compose-specific `.then` (modal-close on `resp.ok && tone !== "error"`), `.catch` (network-error toast), `.finally` (re-enable send btn). Toast display moves into `postForm`.

### 2.3 Refactor `emails.js:90–112` (F5)
Remove local `postForm`; repoint its call sites to `window.postForm` (identical signature incl. array handling). Grep `emails.js` for `postForm(` first to enumerate call sites.

## Phase 3 — Tests (TDD where noted)

### 3.1 Rate limiter — `src/tests/test_auth.py`
`test_rate_limiter_is_generic_key`, `test_login_alias_is_rate_limiter`, `test_send_rate_limiter_singleton_defaults` (asserts 10/60 contract).

### 3.2 Send endpoint rate-limit — `src/tests/test_web.py` (TestSendEmail)
`test_send_rate_limited_returns_warning_toast`: set `auth.send_rate_limiter = RateLimiter(max_attempts=2, …)` in a `try/finally` (singleton-reset idiom), send 2× (consume budget), 3rd returns 204 + `X-Toast-Tone: warning` + "too quickly"; assert `mock_send.call_count == 2`.

### 3.3 `decode_str` charset — `src/tests/test_email_reader.py`
`test_decode_str_unknown_charset_falls_back`: feed `=?x-unknown-8bit?B?SGk=?=`; assert no raise, returns str. (Verify `decode_header` returns the unknown charset label at impl time; adjust fixture if needed.)

### 3.4 Subject bare-`\r` injection — `src/tests/test_smtp.py` (TestBuildMessage)
`test_rejects_bare_cr_in_subject`: `pytest.raises(ValueError)` on `"hi\rextra"`.

### 3.5 Forward-recipient-blank — `src/tests/test_web.py` (TestComposePartial)
`test_forward_partial_leaves_to_blank` (regex-parse the `to` input's `value=` attr, assert empty) + `test_reply_partial_prefills_sender` (regression guard).

### 3.6 `strip_quoted_history` lookahead — `src/tests/test_email_reader.py` (write BEFORE 1.5)
`test_does_not_cut_on_prose_from_line` (new behavior) + `test_still_cuts_outlook_block_with_following_subject` (regression guard, superset of existing Outlook test).

## Phase 4 — Verify & docs
- `python -m pytest src/tests/ -q` — all green, no skips. Watch the locale `wrote:` tests (Dutch/French/German/Spanish/Macedonian/Russian) as the F6 canary.
- `ROADMAP.md`: note send rate limit landed.
- (Optional smoke) send reply → toast+modal-close; spam 11× → warning toast on 11th, modal stays open; forward → blank "To".

## Implementation order (dependency-safe)
1. **3.6 tests first** (F6 canary) → 1.5 → run parser tests.
2. 1.1 (limiter) → 1.2 (endpoint) → 3.1 + 3.2 (limiter tests).
3. 1.3 → 3.3; 1.4 → 3.5; 3.4 (independent, batch with 3.3).
4. 2.1 → 2.2 + 2.3 (frontend DRY).
5. Full suite → ROADMAP.

## Out of scope (explicitly)
- Multi-provider SMTP support (Gmail-only per your note — future work).
- Routing sends through the bounded `_imap_executor` instead of the unbounded default executor — a perf refinement; the rate limit (F1) is the actual abuse guard. Noted for later.
- The remaining minor test gaps from the review (combined send→sync dedup test, connection-establishment failure test, boundary-length tests) — not part of "fix these issues"; can be a follow-up if you want them.