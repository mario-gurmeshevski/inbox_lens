# Inbox Lens

Fetch emails from a Gmail inbox via IMAP, cache them in a local SQLite database, and scan with keyword-based priority tagging. Includes a web dashboard for monitoring with proactive alerts.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Testing](#testing)
- [Host Web](#host-web)
- [Remote Access](#remote-access)
- [Configuration](#configuration)
- [Workflow](#workflow)
- [Performance](#performance)
- [Database](#database)
- [Commands](#commands)
- [License](#license)

## Prerequisites

- Python 3.12+

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

   Defaults work out of the box for Gmail — only edit `.env` if you need non-Gmail IMAP servers or other overrides. See [Configuration](#configuration) for all options.

3. **Run the web dashboard** (Docker with auto-restart & persistent storage):

   ```bash

   make up # For Mac/Linux
   ./commands.ps1 up # For Windows

   make up-ts # For Mac/Linux - Remote Access Using Tailscale
   ./commands.ps1 up-ts # For Windows - Remote Access Using Tailscale

   ```

   Open `http://localhost:8000` — on first visit you'll be prompted to connect your email account. You'll need a Gmail [App Password](https://myaccount.google.com/apppasswords) (2-Step Verification must be enabled).

4. **Define your keywords** (optional):

   `keywords.json` is **auto-created** from `keywords.example.json` (with sensible defaults) on the first scan — works for both Docker and local dev. Edit it to customize priority levels:

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

   To pre-create or edit it before the first scan (optional):

   ```bash

   cp src/data/keywords.example.json src/data/keywords.json

   ```

   Categories are **numeric priority levels from 1 to 10**, where **10 is highest priority** and **1 is lowest**. Each level contains words or phrases to match against email subjects and bodies.

   For running without Docker, remote access via Tailscale, and other options, see [Host Web](#host-web) and [Remote Access](#remote-access).

## Testing

The project includes 415 tests covering all modules. Tests use temporary databases and mock external services (no IMAP credentials needed).

```bash

make test # For Mac/Linux
./commands.ps1 test # For Windows

make test-cov   # For Mac/Linux
./commands.ps1 test-cov # For Windows

```

### Test structure

| File                   | Tests | Coverage                                            |
| ---------------------- | ----- | --------------------------------------------------- |
| `test_cache.py`        | 105   | DB ops, hashing, scanning, search, threads          |
| `test_email_reader.py` | 65    | Parsing, body cleaning, thread extraction, keywords |
| `test_web.py`          | 55    | FastAPI endpoints, SSE, Tailscale, middleware       |
| `test_imap.py`         | 67    | IMAP helpers, connection, fetch, delete             |
| `test_idle_monitor.py` | 60    | IDLE loop, ConnectionLost, run_initial_fetch        |
| `test_crypto.py`       | 22    | Encryption, settings, credentials                   |
| `test_event_bus.py`    | 13    | Pub/sub, synchronous/asynchronous dispatch          |
| `test_utils.py`        | 20    | Keyword parsing, formatting, priorities             |
| `test_constants.py`    | 8     | Env var defaults and overrides                      |

### Linting

```bash

make lint # For Mac/Linux

./commands.ps1 lint # For Windows

```

## Host Web

The web dashboard is a FastAPI app that can be hosted via Docker or run directly on your machine.

### Docker (Recommended)

Runs in a container with automatic restarts and persistent storage.

**Default mode** — accessible at `http://localhost:8000`:

```bash

make up # For Mac/Linux

./commands.ps1 up # For Windows

# or: docker compose up -d

```

**Tailscale mode** — accessible only via your [tailnet](#remote-access) (no port exposed):

```bash

make up-ts # For Mac/Linux

./commands.ps1 up-ts # For Windows

```

On first visit you'll be redirected to the setup page to connect your email account.

**Data persistence** — `docker-compose.yaml` mounts `./src/data:/app/src/data` so the database, encryption key, and keywords survive container restarts and image updates.

**Compose files** — `make up` auto-loads `docker-compose.override.yaml` on top of `docker-compose.yaml`, which publishes port `8000` to the host. `make up-ts` instead uses `-f docker-compose.yaml -f docker-compose.tailscale.yaml`, deliberately skipping the override so no host port is exposed and the dashboard is reachable only via your [tailnet](#remote-access).

**Healthcheck** — the container defines a Docker `HEALTHCHECK` against `GET /health` (served by the FastAPI app), so `docker ps` and orchestrators report live status.

**Updating keywords** — `keywords.json` is auto-created from `keywords.example.json` on the first scan (Docker and local). Edit `./src/data/keywords.json` at any time — changes are picked up on the next scan (no rebuild needed).

To stop the container:

```bash

make down # For Mac/Linux

./commands.ps1 down # For Windows

# or: docker compose down

```

### Local

Install dependencies, then run the dashboard directly with uvicorn (auto-reloads on file changes):

```bash

make install # For Mac/Linux
./commands.ps1 install # For Windows

make web # For Mac/Linux
./commands.ps1 web # For Windows

```

For development (includes test and lint tools):

```bash

make dev-install # For Mac/Linux

./commands.ps1 dev-install # For Windows

```

Opens at `http://localhost:8000`. Set `WEB_HOST` and `WEB_PORT` in `.env` to customize.

### Features

- **Dashboard** — stats cards (total, headers only, fetched, checked, unscanned), priority distribution bar, recent emails

- **Live auto-refresh** — Server-Sent Events (SSE) updates the dashboard and email list in real-time as new emails arrive via IMAP IDLE — no page reload needed

- **Email list** — filterable by status, priority, and search text; paginated; responsive card layout on mobile

- **Email detail** — full body view, colored keyword tags, delete button

- **Settings page** — toggle network access (bind to `0.0.0.0` vs `127.0.0.1`), view local IPs and access URLs

- **Account page** — view connected email address, masked password, disconnect button

- **Responsive** — mobile-friendly with adaptive layouts, touch-optimized controls, and safe-area support for notched devices

### Limitations

- Set `IMAP_SERVER` in `.env` if you're not using Gmail (loaded automatically via Docker `env_file`).

## Remote Access

### Access the Dashboard Remotely with Tailscale

The dashboard has no built-in authentication — it relies on network-level access control. [Tailscale](https://tailscale.com) is an easy way to access the dashboard securely from anywhere.

**Prerequisites**: Install the [Tailscale app](https://tailscale.com/download) on your **remote device**, and make sure you're logged into your Tailscale account. You'll need a [Tailscale account](https://login.tailscale.com/start) (free for personal use).

#### Containerized Tailscale (Docker — optional)

Run the dashboard in Docker with a [Tailscale sidecar](https://tailscale.com/docs/features/containers/docker/how-to/connect-docker-container) — no host-level Tailscale installation required. The app is accessible **only** via your tailnet (no host port mapping).

1. **Start the containers:**

   ```bash

   make up-ts # For Mac/Linux

   ./commands.ps1 make-ts # For Windows

   ```

2. **Authorize the node** (first run only):

   ```bash

   make tailscale-up # For Mac/Linux

   ./commands.ps1 tailscale-up # For Windows

   ```

   Look for a login URL in the output, visit it, and approve the device in your Tailscale admin console. State is persisted in `./tailscale-state/`, so subsequent restarts are automatic.

3. **Find the Tailscale IP:**

   ```bash

   make tailscale-ip # For Mac/Linux

   ./commands.ps1 tailscale-ip # For Windows

   ```

4. **Open the dashboard** at `http://inbox-lens:8000` (via [MagicDNS](https://tailscale.com/docs/features/magicdns)) or `http://<tailscale-ip>:8000` from any device on your tailnet.

> **Automated login (optional):** For unattended deployments, generate an [auth key](https://login.tailscale.com/admin/settings/keys), uncomment the `TS_AUTHKEY` line in `docker-compose.tailscale.yaml`, and paste your key. The container will join your tailnet automatically — no manual URL visit needed.

**Other Tailscale commands:**

```bash
make tailscale-status # For Mac/Linux
./commands.ps1 tailscale-status # For Windows

make tailscale-logout # For Mac/Linux
./commands.ps1 tailscale-logout # For Windows
```

#### Host-Level Tailscale (local dev)

If you're running the dashboard locally (not in Docker) with Tailscale installed on your host:

**Mesh Access (simplest)** — every device on your tailnet gets a private IP. As long as **Network Access** is enabled in the dashboard Settings page (binds to `0.0.0.0`), the dashboard is reachable from any device:

```bash

tailscale ip -4

```

Then open `http://<tailscale-ip>:8000` on your remote device.

**Tailscale Serve — HTTPS** — for automatic TLS and a clean URL without a port number:

```bash

tailscale serve --bg 8000

```

The dashboard is now available at `https://<hostname>.<tailnet-name>.ts.net` with a valid TLS certificate.

> **Having trouble on mobile?** If you see "address not found," your mobile browser may be using DNS-over-HTTPS (DoH), which bypasses Tailscale's VPN DNS. Disable it: **Firefox** → Settings → Private Browsing → DNS-over-HTTPS → Off; **Chrome** → Settings → Privacy → Use secure DNS → Off. Alternatively, set Android **Private DNS** to **Off** in system network settings.

## Configuration

### `.env` file

| Variable          | Default                  | Description                                                        |
| ----------------- | ------------------------ | ------------------------------------------------------------------ |
| `IMAP_SERVER`     | `imap.gmail.com`         | IMAP server address                                                |
| `WEB_HOST`        | `0.0.0.0`                | Web dashboard host                                                 |
| `WEB_PORT`        | `8000`                   | Web dashboard port                                                 |
| `HOST_IP`         | —                        | Host IP for network access display (auto-detected)                 |

The database, encryption key, and keywords file live under `src/data/` (`/app/src/data/` in Docker) and are fixed to that location — they are not configurable via `.env`. Email credentials are configured at runtime via the web setup page — not in `.env`.

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

- **Black** — level 9-10 (critical)

- **Red** — level 7-8 (high)

- **Orange** — level 4-6 (medium)

- **Yellow** — level 1-3 (low)

- **Light gray** — unclassified

## Workflow

Use the **web dashboard** (`make web`) to fetch, scan, and manage emails.

## Performance

By default, email fetching uses **8 parallel IMAP connections** (`MAX_WORKERS = 8`, hardcoded in `src/scripts/email_reader/imap.py` — not an env var). Each worker opens its own connection and fetches a slice of the email IDs, significantly reducing total fetch time.

- Keyword scanning runs in a separate pool of up to 4 workers (`src/scripts/cache/scanner.py`)

- All emails are stored in a SQLite database with WAL mode for fast concurrent reads

- HTML-only emails are automatically converted to clean plain text for keyword scanning

## Database

All emails are stored in a SQLite database (`emails.db` by default) with the following schema:

- Each email is stored with its full metadata, body, keyword matches, thread info, and status (`fetched`, `checked`, or `headers_only`)

- Keyword matches are stored as JSON for flexible querying

- Thread grouping via `thread_id` extracted from email headers

- Indexes on status, category, thread, and date ensure fast lookups

- WAL mode is enabled for safe concurrent access

## Commands

### For Mac/Linux

| Target                  | Description                                                  |
| ----------------------- | ------------------------------------------------------------ |
| `make install`          | Install Python dependencies                                  |
| `make uninstall`        | Uninstall Python dependencies                                |
| `make dev-install`      | Install dev dependencies (test/lint)                         |
| `make web`              | Run the web dashboard                                        |
| `make up`               | Build and start Docker container (default mode, port 8000)   |
| `make up-ts`            | Build and start Docker with Tailscale sidecar (tailnet only) |
| `make down`             | Stop and remove Docker containers                            |
| `make test`             | Run the test suite                                           |
| `make lint`             | Run the linter                                               |
| `make clean`            | Remove build artifacts                                       |
| `make reset`            | Delete DB, WAL files, and secret key                         |
| `make tailscale-up`     | Show Tailscale logs (login URL on first run)                 |
| `make tailscale-status` | Show Tailscale connection status                             |
| `make tailscale-ip`     | Print the Tailscale IPv4 address                             |
| `make tailscale-logout` | Log out of the tailnet                                       |
| `make purge`            | Logout Tailscale, remove Docker, delete data files           |

### For Windows

| Target                            | Description                                                  |
| --------------------------------- | ------------------------------------------------------------ |
| `./commands.ps1 install`          | Install Python dependencies                                  |
| `./commands.ps1 uninstall`        | Uninstall Python dependencies                                |
| `./commands.ps1 dev-install`      | Install dev dependencies (test/lint)                         |
| `./commands.ps1 web`              | Run the web dashboard                                        |
| `./commands.ps1 up`               | Build and start Docker container (default mode, port 8000)   |
| `./commands.ps1 up-ts`            | Build and start Docker with Tailscale sidecar (tailnet only) |
| `./commands.ps1 down`             | Stop and remove Docker containers                            |
| `./commands.ps1 test`             | Run the test suite                                           |
| `./commands.ps1 lint`             | Run the linter                                               |
| `./commands.ps1 clean`            | Remove build artifacts                                       |
| `./commands.ps1 reset`            | Delete DB, WAL files, and secret key                         |
| `./commands.ps1 tailscale-up`     | Show Tailscale logs (login URL on first run)                 |
| `./commands.ps1 tailscale-status` | Show Tailscale connection status                             |
| `./commands.ps1 tailscale-ip`     | Print the Tailscale IPv4 address                             |
| `./commands.ps1 tailscale-logout` | Log out of the tailnet                                       |
| `./commands.ps1 purge`            | Logout Tailscale, remove Docker, delete data files           |

## License

[MIT](LICENSE)
