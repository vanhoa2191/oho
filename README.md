# OHO — Orchestration Hub

A self-hosted control plane for dispatching coding workflows across macOS and Linux computers, with DAG scheduling and explicit human approval gates.

> **Release maturity: beta / pilot**. The control plane, React dashboard and worker are implemented and have an automated test suite; this is not yet an audited multi-tenant production orchestrator. See [Limitations](#limitations).

## Components

| Component | Technology | Purpose |
|---|---|---|
| Control plane | FastAPI + SQLAlchemy + PostgreSQL | Projects, DAG tasks, leases, approvals, audit events |
| Dashboard | React + TypeScript + Nginx | Node fleet, run history, task review |
| Worker | Python 3.12+ | HTTPS polling, local isolated Git worktrees, headless Codex |
| Reverse proxy | Existing host Caddy | TLS and routing; Cloudflare Access enforces administrator identity |

## Authentication modes

- **Local development:** `OHO_AUTH_MODE=bearer` uses an OHO Admin Access Token.
- **Production behind Cloudflare Access:** set `OHO_AUTH_MODE=cloudflare_access`, `OHO_ACCESS_ISSUER`, `OHO_ACCESS_AUD`, and `OHO_ADMIN_EMAIL`. The dashboard then has no separate OHO token prompt. The API validates the signed `Cf-Access-Jwt-Assertion` JWT (signature, issuer, audience, expiry) and requires the exact configured administrator email.

See [docs/cloudflare-access.md](docs/cloudflare-access.md) before switching an existing deployment.

## Quick start (local)

1. Copy `.env.example` to `.env`, replace all placeholder secrets. The password in `OHO_DATABASE_URL` must match `POSTGRES_PASSWORD`.
2. Run `docker compose up -d --build`.
3. In local bearer mode, open `http://127.0.0.1:8788` on the host (or through a trusted SSH tunnel) and enter `OHO_ADMIN_TOKEN`. For Cloudflare Access mode, open the hostname through Cloudflare Access instead.
4. Create a project; create a run from the dashboard JSON form. Example DAG:

```json
[
  {"key": "hello", "kind": "command", "argv": ["echo", "hello"]},
  {"key": "tests", "kind": "command", "argv": ["pytest", "-q"],
   "depends_on": ["hello"]}
]
```

Testing executes code, so it requires approval. Unsafe Git operations receive the `merge` or `deploy` risk tier but the worker still refuses them; authorization is not equivalent to execution capability.

5. Set up workers following [worker/README.md](worker/README.md) or the detailed [first-worker runbook](docs/first-worker.md).

## Local development & validation

```sh
cd server
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt -r ../worker/requirements.txt
python -m pytest -q
cd ../dashboard
npm ci
npm run build
```

## Deploy to VPS

Read [docs/deployment.md](docs/deployment.md). Only the dashboard is bound to host loopback. Use a dedicated hostname and place it behind Cloudflare Access.

## Safety guarantees

- Cloudflare Access mode verifies signed administrator JWTs and checks the exact owner email; worker enrollment and node credentials remain separate.
- Explicit approval required for agent execution, arbitrary executables and Git write commands.
- Execution never uses `shell=True`; worker enforces a second independent allowlist.
- Per-task Git worktrees avoid modifying the operator's main checkout.
- Task claim uses database row locking, renewable leases and stale-token rejection.
- No automatic merge, push, deployment or privileged system changes.

## Limitations

- Headless Codex is the only wired coding adapter. Claude, Gemini, OpenCode and Orca integration require CLI-contract validation and adapter-specific tests.
- The UI is polling-based; terminal streaming, budgets/cost metering and workflow templates are not implemented.
- Worktree isolation is not an OS sandbox. Use a dedicated unprivileged worker account and machine.
- `create_all()` handles initial schema but not future migrations. Introduce Alembic before upgrading schemas in an existing installation.
- Failover is best-effort and subject to local worktree availability and approval policy.
- No production deployment or merge has been performed automatically by this repository.
