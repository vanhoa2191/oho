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

### Recommended: Cloudflare Access administrator login

Follow [cloudflare-access.md](cloudflare-access.md) to obtain the team issuer, OHO application's AUD tag and exact owner email. Configure these in `.env`:

```dotenv
OHO_AUTH_MODE=cloudflare_access
OHO_ACCESS_ISSUER=https://<team-name>.cloudflareaccess.com
OHO_ACCESS_AUD=<exact-OHO-Access-application-AUD-tag>
OHO_ADMIN_EMAIL=<exact-email-allowed-by-Access>
OHO_ENROLLMENT_TOKEN=<existing-random-worker-enrollment-token>
```

Use actual values, without angle brackets. In this mode the browser does not send an OHO Admin Token; the API independently validates Cloudflare's signed JWT and checks the configured email. Do not set an empty or placeholder issuer, AUD tag or email. `OHO_ADMIN_TOKEN` is unused in this mode and can be removed only after the new mode passes smoke tests. Keep a secure rollback copy until then.

### Local development / fallback bearer mode

```dotenv
OHO_AUTH_MODE=bearer
OHO_ADMIN_TOKEN=<independent-random-admin-token>
OHO_ENROLLMENT_TOKEN=<different-random-enrollment-token>
```

The password in `OHO_DATABASE_URL` must match `POSTGRES_PASSWORD`. Use URL-safe hexadecimal passwords or URL-encode reserved characters. For an existing deployment, preserve the current DB password and URL; do not regenerate them or reinitialize the volume.

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

Use Cloudflare Access with an exact-email Allow policy. In `cloudflare_access` mode, the API verifies the signed `Cf-Access-Jwt-Assertion` itself, so direct-origin requests without a valid Access JWT cannot access admin endpoints. Do not add a broad Bypass policy.

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
