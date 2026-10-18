# One-time worker gateway setup (VPS)

Goal: keep the OHO dashboard behind Cloudflare Access while simplifying worker onboarding to one OHO enrollment token. This setup is done once, not on each workstation.

## Security design

- Dashboard: `https://oho.tienganhcungcon.online`, protected by Cloudflare Access and API-side JWT validation.
- Worker API: `https://workers.tienganhcungcon.online`, proxied through Cloudflare but not covered by an Access application.
- Dedicated Nginx gateway binds only to `127.0.0.1:8789`.
- Gateway forwards only `/api/v1/nodes/*`; every other path returns 404.
- Enrollment requires the high-entropy `OHO_ENROLLMENT_TOKEN`; each enrolled node then uses its own node credential. Admin routes remain protected by API-side bearer/JWT auth even if attempted via the worker hostname.
- Enrollment requests are rate-limited. Do not create a broad Access Bypass policy and do not publish port 8789 directly.

## 1. Deploy gateway

Update `/opt/oho` to a reviewed commit containing `deploy/nginx/worker-gateway.conf` and the `worker-gateway` Compose service. Preserve `.env`, the PostgreSQL volume and all existing services.

The Compose service must use `nginx:1.27-alpine`, mount `./deploy/nginx/worker-gateway.conf:/etc/nginx/conf.d/default.conf:ro`, depend on API health, publish only `127.0.0.1:8789:80`, join the private `internal` Docker network and restart unless stopped.

Validate `docker compose config --quiet` before applying. Start only the new gateway service; never run `down`, delete volumes or restart Docker.

## 2. DNS

In Cloudflare DNS for `tienganhcungcon.online`, add A record:
- Name: `workers`
- IPv4: the same public VPS IP used by OHO
- Proxy status: Proxied (orange cloud)

Confirm no wildcard Access application covers `workers.tienganhcungcon.online`. Keep the existing OHO Access application and exact-email Allow policy unchanged.

## 3. Caddy

Back up the Caddyfile, then add one new site block without modifying existing blocks:

```caddyfile
workers.tienganhcungcon.online {
    encode zstd gzip
    header {
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
    reverse_proxy 127.0.0.1:8789
}
```

Inspect the current TLS setup. If OHO uses a manually specified Origin CA certificate, do not reuse it unless the certificate SAN contains the worker hostname. Prefer Caddy automatic HTTPS for this new hostname if supported, or create a Cloudflare Origin CA certificate that includes `workers.tienganhcungcon.online`. Keep Cloudflare SSL/TLS at Full (strict). Validate before reload and reload only Caddy.

## 4. Smoke tests

From the VPS, the worker gateway root should return 404 (it deliberately serves no dashboard):

```bash
curl -sS -o /dev/null -w 'Worker gateway root HTTP %{http_code}\n' https://workers.tienganhcungcon.online/
```

Expected: 404. A request to `/api/v1/overview` must return 404 at the gateway. An enrollment request with no/invalid token must not create a node and should return 401 or rate-limit 429. Never place the real enrollment token in command arguments or logs.

## 5. Onboard workstations

After gateway TLS and routing pass, each workstation runs `bash worker/install.sh` from the OHO checkout. It asks for a worker name, API URL, local project path and the one enrollment token, then configures and registers the node.

Do not enroll the other three computers until the first worker passes the echo task.

## Rollback

If the gateway or TLS change fails, remove only the new Caddy site block, validate and reload Caddy, then stop only the worker-gateway service. Do not modify the OHO dashboard site, Cloudflare Access policy, API/database services or volumes.
