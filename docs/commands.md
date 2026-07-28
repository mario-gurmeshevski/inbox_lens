# Commands

## For Mac/Linux

| Target                  | Description                                                  |
| ----------------------- | ------------------------------------------------------------ |
| `make install`          | Install Python dependencies (project-local `.venv/`)         |
| `make uninstall`        | Delete `.venv/`                                              |
| `make dev-install`      | Install dev dependencies (test/lint)                         |
| `make web`              | Run the web dashboard                                        |
| `make up`               | Build and start Docker container (default mode, port 8000)   |
| `make up-ts`            | Build and start Docker with Tailscale sidecar (tailnet only) |
| `make down`             | Full teardown: stop containers, remove images + volumes      |
| `make stop`             | Stop containers without removing them (preserves data)       |
| `make start`            | Restart containers previously stopped with `make stop`       |
| `make restart`          | Restart running containers in place (preserves data)         |
| `make test`             | Run the test suite                                           |
| `make test-cov`         | Run the test suite with coverage                             |
| `make lint`             | Lint Python (Ruff) + unused code (Vulture) + templates (djlint) |
| `make format`           | Format Python (Ruff) + templates (djlint)                    |
| `make unused`           | Find unused code: imports/functions/classes (Vulture)        |
| `make pre-commit`       | Format, lint (incl. Vulture), and test                       |
| `make clean`            | Remove build artifacts                                       |
| `make reset`            | Delete DB, WAL files, secret key, and session key            |
| `make tailscale-up`     | Show Tailscale logs (login URL on first run)                 |
| `make tailscale-status` | Show Tailscale connection status                             |
| `make tailscale-ip`     | Print the Tailscale IPv4 address                             |
| `make tailscale-logout` | Log out of the tailnet                                       |
| `make purge`            | Logout Tailscale, remove Docker, delete data files           |

## For Windows

| Target                            | Description                                                  |
| --------------------------------- | ------------------------------------------------------------ |
| `./commands.ps1 install`          | Install Python dependencies (project-local `.venv/`)         |
| `./commands.ps1 uninstall`        | Delete `.venv/`                                              |
| `./commands.ps1 dev-install`      | Install dev dependencies (test/lint)                         |
| `./commands.ps1 web`              | Run the web dashboard                                        |
| `./commands.ps1 up`               | Build and start Docker container (default mode, port 8000)   |
| `./commands.ps1 up-ts`            | Build and start Docker with Tailscale sidecar (tailnet only) |
| `./commands.ps1 down`             | Full teardown: stop containers, remove images + volumes      |
| `./commands.ps1 stop`             | Stop containers without removing them (preserves data)       |
| `./commands.ps1 start`            | Restart containers previously stopped with `stop`            |
| `./commands.ps1 restart`          | Restart running containers in place (preserves data)         |
| `./commands.ps1 test`             | Run the test suite                                           |
| `./commands.ps1 test-cov`         | Run the test suite with coverage                             |
| `./commands.ps1 lint`             | Lint Python (Ruff) + unused code (Vulture) + templates (djlint) |
| `./commands.ps1 format`           | Format Python (Ruff) + templates (djlint)                    |
| `./commands.ps1 unused`           | Find unused code: imports/functions/classes (Vulture)        |
| `./commands.ps1 pre-commit`       | Format, lint (incl. Vulture), and test                       |
| `./commands.ps1 clean`            | Remove build artifacts                                       |
| `./commands.ps1 reset`            | Delete DB, WAL files, secret key, and session key            |
| `./commands.ps1 tailscale-up`     | Show Tailscale logs (login URL on first run)                 |
| `./commands.ps1 tailscale-status` | Show Tailscale connection status                             |
| `./commands.ps1 tailscale-ip`     | Print the Tailscale IPv4 address                             |
| `./commands.ps1 tailscale-logout` | Log out of the tailnet                                       |
| `./commands.ps1 purge`            | Logout Tailscale, remove Docker, delete data files           |
