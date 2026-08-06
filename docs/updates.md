# Updates

## Docker

The app checks for updates every 6 hours. An **update banner** appears when one is available, and **Settings → Updates** lets you manually check or trigger an update.

**One-click update**: if the Docker socket is mounted, **Update Now** pulls the latest image and recreates the container in place. The app is briefly unavailable during the swap.

> **Security note:** mounting `/var/run/docker.sock` grants the container host-equivalent root, anything that can drive the Docker engine can create a privileged container and escape to the host.
>
> One-click updates require **full container lifecycle control** (create, start, stop, rename, update, and delete containers, plus image pulls), so there is no way to sandbox the socket while keeping one-click updates working. The only complete mitigation is to **remove the `/var/run/docker.sock` mount** from `docker-compose.yaml` (this disables one-click updates, you'll update manually via `docker compose pull && docker compose up -d`).
>

**Socket permissions:** When the Docker socket is mounted, the container automatically grants `appuser` read/write access: it detects the socket's group ID and adds the user to that group on startup, so one-click updates work without extra configuration. If your setup prevents this, fall back to either:

```yaml
user: root # Option A
group_add: ["<host docker gid>"] # Option B
```

**No socket?** The button shows the manual command instead:

```bash
docker compose pull && docker compose up -d
```

## Non-Docker

The app checks for new releases and shows the current vs. latest version in both the update banner and Settings. One-click self-update isn't available (no container to recreate), the banner points you to the host command. To update a local checkout:

```bash
git pull && make install

# (or `./commands.ps1 install` on Windows).
```

Then restart `make web` (or `./commands.ps1 web` on Windows).

## Releases

Bump `version` in `pyproject.toml`, commit, and push to a branch, then open a PR and merge it into `main` (`main` is protected and only accepts merges). Merging kicks off the automation: image build, version tag, and GitHub Release. Run `docker compose pull` once after the first release to switch to the published image.
