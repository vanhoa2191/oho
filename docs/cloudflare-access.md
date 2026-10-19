# Use Cloudflare Access as OHO's administrator login

OHO supports two administrator authentication modes:

- `bearer`: legacy OHO Admin Access Token.
- `cloudflare_access`: verifies Cloudflare Access's signed JWT at the API, then checks issuer, audience, expiry and the exact administrator email. The dashboard has no separate OHO token prompt in this mode.

## Cloudflare configuration

1. In Cloudflare Zero Trust, open the Self-hosted Access application for `oho.tienganhcungcon.online`.
2. Ensure an `Allow` policy includes only the exact email that will administer OHO. Do not use `Everyone`, a broad email domain, or a `Bypass` policy.
3. Open the application overview/details and copy the **Audience (AUD) Tag**. It is an identifier, not a secret.
4. Find the Cloudflare Access team domain/issuer in Zero Trust settings. It should look like `https://<team-name>.cloudflareaccess.com`.
5. Ensure the OHO hostname is proxied through Cloudflare. The API verifies the signed `Cf-Access-Jwt-Assertion` itself, so a direct-origin request without a valid Access JWT cannot access admin APIs.

## VPS configuration

Set these values in `/opt/oho/.env`:

```dotenv
OHO_AUTH_MODE=cloudflare_access
OHO_ACCESS_ISSUER=https://<team-name>.cloudflareaccess.com
OHO_ACCESS_AUD=<the exact AUD Tag from the OHO Access application>
OHO_ADMIN_EMAIL=<the exact email allowed by the Access policy>
```

Use the actual values, without angle brackets. Do not put secrets in Git or chat. In this mode `OHO_ADMIN_TOKEN` is not used by the API and can be removed after successful verification. Keep `OHO_ENROLLMENT_TOKEN` because worker enrollment still requires it.

Rebuild/recreate the API and web containers only after checking the values. Keep the old bearer token in a secure local backup until the Access-mode test succeeds. Rollback is to set `OHO_AUTH_MODE=bearer`, restore the old admin token, then recreate the API and web containers.

## Worker separation

Worker enrollment and task endpoints retain their independent enrollment/node tokens. If Cloudflare Access Service Auth is used for worker requests, the worker sends its service-token headers from environment variables. A service token does not satisfy the administrator email check: admin APIs require a valid Access JWT whose `email` exactly matches `OHO_ADMIN_EMAIL`.

Never add a broad `Bypass` policy to make workers work. Test human login and worker enrollment separately.
