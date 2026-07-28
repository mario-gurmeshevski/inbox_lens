# Remote Access

## Access the Dashboard Remotely with Tailscale

The dashboard relies on a combination of password [authentication](authentication.md) and network-level access control. [Tailscale](https://tailscale.com) is an easy way to access the dashboard securely from anywhere, and setting a dashboard password lets you expose it safely on your LAN.

**Prerequisites**: Install the [Tailscale app](https://tailscale.com/download) on your **remote device**, and make sure you're logged into your Tailscale account. You'll need a [Tailscale account](https://login.tailscale.com/start) (free for personal use).

1. **Start the containers:**

   ```bash

   make up-ts # For Mac/Linux

   ./commands.ps1 up-ts # For Windows

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

4. **Open the dashboard** at `https://inbox-lens.<tailnet>.ts.net` (via [Tailscale Serve HTTPS](#https-via-tailscale-serve), on by default) or `http://<tailscale-ip>:8000` over plain HTTP.

> **Automated login (optional):** For unattended deployments, generate an [auth key](https://login.tailscale.com/admin/settings/keys), uncomment the `TS_AUTHKEY` line in `docker-compose.tailscale.yaml`, and paste your key. The container will join your tailnet automatically — no manual URL visit needed.

### HTTPS via Tailscale Serve

To use HTTPS via `make up-ts`, follow these steps:

> **Enable HTTPS:** In the Tailscale Admin Console, go to DNS > HTTPS Certificates and click Enable. Ensure MagicDNS is also on.
>
> **Launch:** Run `make up-ts`. The sidecar will automatically provision your certificate (the first load may take 30–60s).
>
> **Access:** Open `https://inbox-lens.<tailnet>.ts.net`.

**Notes:**

- **Fallback:** If HTTPS isn't enabled, the app defaults to HTTP on port `8000`.
- **Disable:** Set `TS_SERVE_HTTPS=false` in `.env` to force HTTP-only mode.
- **Security:** Tailscale traffic is encrypted by default; HTTPS is only needed to remove browser security warnings and use clean URLs.

**Other Tailscale commands:**

```bash
make tailscale-status # For Mac/Linux
./commands.ps1 tailscale-status # For Windows

make tailscale-logout # For Mac/Linux
./commands.ps1 tailscale-logout # For Windows
```

### Host-Level Tailscale (local dev)

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
