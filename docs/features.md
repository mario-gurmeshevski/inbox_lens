# Features

- **Dashboard**: stats cards (total, headers only, fetched, checked, unscanned), priority distribution bar, recent emails

- **Live auto-refresh**: Server-Sent Events (SSE) updates the dashboard and email list in real-time as new emails arrive via IMAP IDLE, with no page reload needed

- **Email list**: filterable by status, priority, and search text, paginated responsive card layout on mobile, bulk-select rows to mark read/unread, star/unstar, archive, delete, or move

- **Email detail**: full body view with rendered Markdown (bold, lists, links, code), colored keyword tags, grouped by conversation (threaded replies with a "Sent" badge on outbound messages)

- **Email actions**: manage mail without leaving the dashboard: mark read/unread and star/unstar (toggle inline), archive, move to folder, delete, and **reply** or **forward** via a compose modal (recipient pre-filled on reply, quoted block on forward, threaded headers). Sent messages are saved locally and the compose endpoint is rate-limited to 10 sends/minute. Delete/archive/move run the IMAP operation first and only update the local cache on success, so the UI never shows a message as gone while it still lives on the server

- **Compose**: a floating compose button plus the reply/forward flows open a modal with **CC/BCC** support, a **Markdown toolbar** (bold, italic, link, list, quote, code), and an HTML part sent alongside the plain-text body. Messages can have multiple recipients, and Bcc recipients are included in the envelope but never appear in the message headers

- **Attachments**: add files when composing, replying, or forwarding. Files are validated server-side (per-file and total size caps, executable types like `.exe`/`.sh`/`.js` are rejected regardless of declared MIME type) and **encrypted at rest** under `src/data/attachments/`. Forwarding fetches only the original's attachment parts from IMAP (via BODYSTRUCTURE, with a full-message fallback) and offers them as removable chips. Attachments persist across drafts and the outbox retry queue, and are cleaned up once a message is sent or discarded

- **Attachments browser**: a dedicated page (`/attachments`) lists attachments received on incoming mail, with a total count and a filter bar. Filter by type bucket (Images, PDFs, Documents, Archives, Other), search by filename or extension (e.g. `invoice` or `.pdf`), and sort by size. Results are paginated and refresh live over SSE, and each row links to its source email and offers a one-click download. Received attachments are cached in the background the first time you open a message (lazy backfill), then become searchable here

- **Drafts**: in-progress emails **autosave** every 2 seconds and are listed on the **Drafts** page (`/drafts`), where each can be resumed or deleted. Stale drafts older than 30 days are pruned on startup

- **Signature**: set a persistent signature (Markdown supported) under **Settings → Signature**, it's appended to every compose and re-synced to the current setting when a draft is resumed, so changing your signature updates existing drafts too

- **Sent view**: outbound mail lives in a dedicated **Sent** inbox (`/emails/sent`) that lists each message individually (no conversation collapsing). By default sent mail is hidden from the main inbox, **Settings → Show sent mails in inbox** toggles them back inline. Sent-folder sync now also refreshes read/starred flags from the server, and resolves the Sent folder by name (not just the `\Sent` attribute) for better compatibility with non-Gmail providers

- **Outbox**: when a send fails (SMTP unreachable, network error), the message is saved to an **Outbox** queue (`/outbox`) and retried automatically in the background with backoff (1m, 5m, 15m, 1h, 4h), up to 6 attempts. From the Outbox page you can retry now, edit and resend, or delete. Failed sends are never silently dropped

- **SMTP settings & test**: override the SMTP host, port, and security mode (SSL/TLS, STARTTLS, or None) under **Settings → Outbound**, and verify the connection with a **Test connection** button before relying on it. Defaults come from your `SMTP_SERVER`/`SMTP_PORT` environment variables

- **Move to folder**: a folder picker populated from your IMAP account's mailbox lists, folder names are validated before being sent to the IMAP server. Sent messages are tracked in their real IMAP folder, so delete/archive/move/star work correctly on outbound mail

- **Keyword editor**: manage priority keywords from the dashboard: inline add/edit/remove words, add/remove priority categories, import/export JSON config, and re-scan cached emails to apply changes retroactively

- **Settings page**: toggle network access (bind to `0.0.0.0` vs `127.0.0.1`), configure your email signature, control whether sent mail appears in the inbox, and view local IPs and access URLs. The settings tabs stick to the nav bar on scroll so you can jump between sections without scrolling back up

- **In-app updates**: a dismissible update banner (shown to Docker and non-Docker users alike) plus a Settings panel with version check. Docker deployments get optional one-click self-update (pulls the new image and recreates the container), non-Docker installs see the host update command. See [Updates](updates.md).

- **Account page**: view connected email address, masked password, disconnect button

- **Responsive**: mobile-friendly with adaptive layouts, touch-optimized controls, and safe-area support for notched devices
