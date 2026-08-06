# Hosting

The web dashboard is a FastAPI app that can be hosted via Docker or run directly on your machine.

## Docker (Recommended)

Runs in a container with automatic restarts and persistent storage.

**Default mode**: accessible at `http://localhost:8000`:

```bash

make up # For Mac/Linux

./commands.ps1 up # For Windows

# or: docker compose up -d

```

**Tailscale mode**: accessible only via your [tailnet](remote-access.md) (no port exposed):

```bash

make up-ts # For Mac/Linux

./commands.ps1 up-ts # For Windows

```

**Host disk**: every successful `make up` / `make up-ts` build automatically prunes dangling images (the orphaned previous build) and unused build cache via `make prune-auto`, so the host doesn't accumulate Docker bloat over time. It never touches volumes, running containers, or tagged images you keep around. For a deeper clean (all unused images + stopped containers), run `make prune` manually.

On first visit you'll be redirected to the setup page to connect your email account.

**Data persistence**: `docker-compose.yaml` persists data in the named `inbox-lens-data` volume so the database (including keyword configuration), encryption key, and session secret survive container restarts and image updates.

**Compose files**: `docker-compose.yaml` publishes port `8000` to the host (`ports: - "8000:8000"`), and `make up` runs plain `docker compose up --build` against it. `make up-ts` instead uses `-f docker-compose.yaml -f docker-compose.tailscale.yaml`, whose override clears the base file's port mapping so no host port is exposed and the dashboard is reachable only via your [tailnet](remote-access.md). The tailscale file uses `ports: !reset []` to undo the base file's port mapping, which requires Docker Compose v2.24+ (the `!reset` tag was introduced then).

**Healthcheck**: the container defines a Docker `HEALTHCHECK` against `GET /health` (served by the FastAPI app), so `docker ps` and orchestrators report live status.

**Managing keywords**: keywords are configured from the dashboard's **Keywords** page (`/keywords`) and stored in the database. Changes apply to newly fetched emails immediately, and the **Re-scan** action re-evaluates already-cached emails, no rebuild needed.

To stop the container:

```bash

make stop # For Mac/Linux

./commands.ps1 stop # For Windows

# or: docker compose -f docker-compose.yaml -f docker-compose.tailscale.yaml stop

```

`make down` / `./commands.ps1 down` is a full teardown: it also removes images and volumes. Use it only when you want a clean slate.

```bash

make down # For Mac/Linux - full teardown (removes images + volumes)

./commands.ps1 down # For Windows

```

## Local

Install dependencies into a project-local `.venv/` (auto-created), then run the dashboard directly with uvicorn (auto-reloads on file changes):

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

> All `make` commands automatically use the `.venv/`, no system-wide installs. Activate it manually with `source .venv/bin/activate` if you want to run Python directly.

Opens at `http://localhost:8000`. Set `WEB_HOST` and `WEB_PORT` in `.env` to customize.

## Limitations

- Set `IMAP_SERVER` in `.env` if you're not using Gmail (loaded automatically via Docker `env_file`).
