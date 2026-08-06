# Inbox Lens

Fetch emails from a Gmail inbox via IMAP, cache them in a local SQLite database, and scan them with keyword-based priority tagging. Includes a web dashboard for monitoring with proactive alerts.

## Prerequisites

- [Docker](https://www.docker.com/) (for the recommended `make up` path)

- Python 3.12+ (only needed for the local `make web` path)

- A Gmail account with 2-Step Verification enabled and an [App Password](https://myaccount.google.com/apppasswords) generated

## Quick Start

1. **Clone the repository**:

   ```bash

   git clone https://github.com/mario-gurmeshevski/inbox_lens.git

   ```

2. **Copy the environment file**:

   ```bash

   cp .env.example .env

   ```

   Defaults work out of the box for Gmail (currently the only supported provider). Only edit `.env` for other overrides, see [Configuration](docs/configuration.md) for all options.

3. **Run the web dashboard** (Docker with auto-restart & persistent storage):

   ```bash

   make up # For Mac/Linux
   ./commands.ps1 up # For Windows

   make up-ts # For Mac/Linux - Remote Access Using Tailscale
   ./commands.ps1 up-ts # For Windows - Remote Access Using Tailscale

   ```

   To halt containers without losing data (no rebuild needed to resume):

   ```bash

   make stop # For Mac/Linux
   ./commands.ps1 stop # For Windows

   make start # For Mac/Linux
   ./commands.ps1 start # For Windows

   ```

> Using `make up-ts`? HTTPS requires MagicDNS + HTTPS Certificates enabled in your [Tailscale admin console](https://login.tailscale.com/admin/dns), see [HTTPS via Tailscale Serve](docs/remote-access.md#https-via-tailscale-serve).

> `make up` publishes port 8000 on the host, access the dashboard at `http://localhost:8000` or `http://<your-lan-ip>:8000` (the LAN IP is printed when you run `make up`). Uses a Docker named volume (`inbox-lens-data`) for persistent storage.

Open `http://localhost:8000`, on first visit you'll be prompted to connect your email account. You'll need a Gmail [App Password](https://myaccount.google.com/apppasswords) (2-Step Verification must be enabled).

4. **Customize your keywords** (optional):

   Priority keywords are **managed from the dashboard** at **Keywords** (`/keywords`), no file editing required. On first run the database is auto-filled from `keywords.json` if it exists (handy for carrying over a prior config), otherwise from the bundled `keywords.example.json` defaults. From the editor you can:
   - Add, edit, and remove individual words inline for any priority level
   - Add or remove entire priority categories (arbitrary positive-integer levels, defaults 1–10)
   - Import / export the configuration as JSON
   - Re-scan already-cached emails to apply new keywords retroactively

   Changes apply to newly fetched emails immediately. The keyword config is stored in the database (in Docker: `/app/src/data/emails.db`), so it persists across restarts with no extra setup.

   Categories are **numeric priority levels**, where **higher is more urgent** (e.g. **10 is highest priority** and **1 is lowest**). Each level contains words or phrases to match against email subjects and bodies. An example config looks like:

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

   For running without Docker, remote access via Tailscale, and other options, see [Hosting](docs/hosting.md) and [Remote Access](docs/remote-access.md).

## Features

- **Dashboard**: stats cards, priority distribution bar, and recent emails
- **Live auto-refresh**: real-time updates over SSE as new emails arrive via IMAP IDLE (no page reload)
- **Filterable email list**: search/filter by status and priority, paginated, with bulk actions (read/unread, star, archive, delete, move)
- **Email detail**: rendered Markdown body, color-coded keyword tags, threaded conversations
- **Reply, forward & compose**: compose modal with CC/BCC, a Markdown toolbar, and an HTML part, 10 sends/minute rate limit. Failed sends are never lost, they queue for automatic retry (see Outbox)
- **Attachments**: add files when composing, replying, or forwarding, forward a message's original attachments straight from IMAP. Files are encrypted at rest and survive the outbox retry queue and drafts
- **Attachments browser**: a dedicated page (`/attachments`) to search, filter, and download attachments received on incoming mail
- **Drafts**: autosave every 2 seconds, resumable on the Drafts page, stale drafts pruned after 30 days
- **Signature**: persistent Markdown signature appended to every compose, re-synced to drafts
- **Sent inbox**: dedicated view for outbound mail, optionally shown inline in the main inbox. Sent-folder sync refreshes read/starred flags from the server and resolves the Sent folder by name for non-Gmail providers
- **Outbox**: when a send can't reach SMTP, the message is saved to an Outbox queue and retried automatically with backoff (1m, 5m, 15m, 1h, 4h) up to 6 attempts. You can manually retry, edit, or delete from the Outbox page
- **Move to folder**: IMAP mailbox picker with validated folder names
- **Keyword editor**: inline add/edit/remove words and categories, JSON import/export, retroactive re-scan
- **Settings page**: network access, signature, sent-mail visibility, local IPs/access URLs, and **Outbound (SMTP)** config: override host/port/security (SSL/TLS, STARTTLS, None) and verify with a Test connection button
- **In-app updates**: dismissible update banner + Settings panel, optional one-click self-update for Docker
- **Account page**: connected address, masked password, disconnect
- **Responsive**: mobile-friendly layouts, touch-optimized controls, safe-area support

See [docs/features.md](docs/features.md) for full details.

## Security

The dashboard requires a password once exposed beyond localhost, see [docs/authentication.md](docs/authentication.md).

## Documentation

- [Features](docs/features.md): full feature descriptions
- [Hosting](docs/hosting.md): Docker and local run options
- [Authentication](docs/authentication.md): dashboard password and API keys
- [Remote Access](docs/remote-access.md): Tailscale setup and HTTPS
- [Configuration](docs/configuration.md): `.env` variables and priority keywords
- [Updates](docs/updates.md): in-app updates, Docker self-update, releases
- [Testing](docs/testing.md): test suite, coverage breakdown, linting
- [Commands](docs/commands.md): `make` and PowerShell targets
- [Internals](docs/internals.md): workflow, performance, and database schema
- [CONTRIBUTING.md](CONTRIBUTING.md): development setup and editor configuration
- [ROADMAP.md](ROADMAP.md): planned features

## License

[MIT](LICENSE)
