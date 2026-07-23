# Roadmap

This document outlines planned features and improvements for Inbox Lens.

## Multi-Account Support

Connect and monitor multiple email accounts simultaneously.

- Add multiple IMAP accounts via the web setup page
- Per-account keyword configuration and priority rules
- Unified inbox view across all accounts with account badges
- Individual account stats on the dashboard
- Isolated or shared SQLite storage per account

## Multi-Provider Support

Expand beyond Gmail to support other major email providers out of the box.

- **Outlook / Hotmail** (`outlook.office365.com`)
- **Yahoo Mail** (`imap.mail.yahoo.com`)
- **Apple Mail / iCloud** (`imap.mail.me.com`)
- Pre-configured IMAP server presets (select provider during setup instead of manual entry)

## Email Actions

Manage emails directly from the dashboard without leaving the UI.

- **Compose Enhancements:** Support for CC/BCC, Rich-text/HTML editing, persistent signatures, and drafts.
- **Attachments:** Upload files with size validation and forward original attachments via IMAP.
- **Outbound Reliability:** Sent folder syncing, an offline outbox queue, and SMTP connection testing in the UI.

## Completed

- **Outbound rate limiting:** Reply/forward sends are throttled to 10 per minute per account (in-memory sliding window) to guard against runaway loops and compromised sessions. A hit limit surfaces a warning toast and leaves the compose modal open so the user can retry.
- **Header-decode robustness:** `decode_str` now falls back to UTF-8 on unknown RFC 2047 charsets instead of aborting a header batch with `LookupError`.
- **Forward recipient UX:** Forwarding no longer pre-fills the original sender as the recipient — "To" starts blank so you pick a new recipient.
- **Quoted-history trimming:** The `From:`-line heuristic in `strip_quoted_history` now requires a following `Date:`/`Subject:` line, so a user-written "From:" line in the body no longer truncates the displayed message.

