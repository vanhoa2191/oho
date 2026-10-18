# Deploy OHO beside Cloud Harness MCP (VPS `hoa`)

The host already runs Cloud Harness MCP, Caddy, and other services. OHO must use **its own project name, network, database volume, localhost port and hostname**. Do not modify existing Cloud Harness firewall, Docker bridge, Caddy MCP route, or Supabase volumes.

## Prerequisites

- Ubuntu/Linux host with Docker Compose v2, sufficient disk/memory, and Caddy.
- A DNS record for a **new hostname**, e.g. `oho.tienganhcungcon.online`, pointing to this VPS through Cloudflare.
- Confirm port 8788 is unused: `sudo ss -lntp | grep ':8788' || true`.
- A reviewed, approved release branch or tag. Do not deploy an unreviewed feature branch to production.

## Environment (do not commit these values)

```bash
cd /opt
sudo git clone https://github.com/vanhoa2191/oho.git oho
cd /opt/oho
# Checkout ONLY an approved release ref; feature branches require review first.
sudo cp .env.example .env
sudo chmod 600 .env
```

Generate three different high-entropy values using `openssl rand -hex 32` (two bearer tokens and DB password). Update `.env`:

```dotenv
OHO_ADMIN_TOKEN=<64hex-random>
OHO_ENROLLMENT_TOKEN=<different-64hex-random>
POSTGRES_PASSWORD=<third-64hex-random>
OHO_DATABASE_URL=postgresql+psycopg://oho:<same-db-password>@db:5432/oho
OHO_PORT=8788
```

Use a URL-safe password (hex) to avoid percent-encoding issues. Keep `.env` readable only by the deployment account.

## Start and verify (explicitly approved production action)

```bash
cd /opt/oho
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8788/health
```

Expected API health: `{"ok":true,"service":"oho"}`. API and database are **not** published on public interfaces. Check logs with `docker compose logs --tail=100 api web db` but never post secrets.

## Caddy

Add a **new site block** to the existing host Caddyfile (illustrative; adapt to actual file paths):

```caddyfile
oho.tienganhcungcon.online {
    encode zstd gzip
    header {
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        Strict-Transport-Security "max-age=31536000"
    }
    reverse_proxy 127.0.0.1:8788
}
```

Check `caddy validate --config /etc/caddy/Caddyfile` before a carefully scheduled reload. Ensure Caddy is running on the host; if it is in Docker, `127.0.0.1` inside its container does **not** refer to the host—use a dedicated shared Docker network instead, without exposing OHO to the Internet except via TLS Caddy. Because Cloud Harness MCP is already using this domain family, **never point OHO at `mcp.tienganhcungcon.online`**.

Before public access, place the dashboard behind Cloudflare Access (or a similarly strong external authentication layer); the admin bearer token alone is insufficient for high-threat deployment.

## Provision workers

On each of the two macOS and two Linux Desktop machines, follow `worker/README.md`. Every node must use a unique name and its own dedicated project checkout. Enroll with the enrollment secret, then remove the enrollment secret from the worker shell environment. Use a native service manager (launchd/systemd) to supervise the process only after running an interactive smoke test.

## Rollback

For an application-only rollback, check out a previous approved release tag and rebuild the API/web services without `docker compose down -v`. Back up the PostgreSQL volume/database before schema changes. Do not run `docker system prune` on this shared VPS.

## Operational checklist

- Confirm service health and login.
- Create a project and harmless `echo` task.
- Enroll a Linux test worker, verify claim/completion and event log.
- Test a `pytest` task requiring approval; reject/approve as desired.
- Simulate worker outage and verify leases retry after 90 seconds.
- Check private networking, access logs, backups, firewall persistence, and existing Caddy routes.
- Add the remaining three workers once the staging test has passed.

**Not completed automatically:** DNS creation, live Caddy changes, VPS Docker Compose launch, four physical worker installations, credentials, and production approval.
