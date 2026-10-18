# OHO — Orchestration Hub

A self-hosted control plane for dispatching coding workflows across macOS and Linux computers, with DAG scheduling and explicit human approval gates.

> **Release maturity: beta / pilot**. The control plane, React dashboard and worker are implemented and have an automated test suite; this is not yet an audited multi-tenant production orchestrator. See [Limitations](#limitations).

## Components

| Component | Technology | Purpose |
|---|---|---|
| Control plane | FastAPI + SQLAlchemy + PostgreSQL | Projects, DAG tasks, leases, approvals, audit events |
| Dashboard | React + TypeScript + Nginx | Node fleet, run history, task review |
| Worker | Python 3.12+ | HTTPS polling, local isolated Git worktrees, headless Codex |
| Reverse proxy | Existing host Caddy | TLS, optional extra access control |

## Quick start (local)

1. Copy `.env.example` to `.env`, replace **all** default secrets. The password portion of `OHO_DATABASE_URL` must match `POSTGRES_PASSWORD` (URL-encode special characters).
2. Run `docker compose up -d --build`.
3. Open `http://127.0.0.1:8788` **on the host** (or through a trusted SSH tunnel). Enter `OHO_ADMIN_TOKEN` in the dashboard login.
4. Create a project; create a run from the dashboard JSON form. Example DAG:

```json
[
  {"key": "hello", "kind": "command", "argv": ["echo", "hello"]},
  {"key": "tests", "kind": "command", "argv": ["pytest", "-q"],
   "depends_on": ["hello"]}
]
```

Testing executes code, so it requires approval. Unsafe Git operations receive the `merge` or `deploy` risk tier but the worker still **refuses** them; authorization is not equivalent to execution capability.

5. Set up workers following [worker/README.md](worker/README.md). For one machine per network, each makes outbound HTTPS requests only.

### Local development & validation

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

Read [docs/deployment.md](docs/deployment.md). Only the dashboard is bound to host loopback. Use a **new dedicated hostname** with Caddy (do not repurpose the Cloud Harness MCP hostname).

## Safety guarantees in this release

- Distinct administrator and enrollment secrets; node-specific hashed tokens stored in PostgreSQL.
- Explicit approval required for agent execution, arbitrary executables and Git write commands.
- Execution never uses `shell=True`; worker enforces a second independent allowlist.
- Worktree isolation to avoid modifying the operator's main project checkout.
- Task claim guarded by PostgreSQL row locking; short renewable leases and stale-token rejection.
- No automatic merge, push, deployment or privileged system changes.

## Limitations

- Headless **Codex** is the only wired coding adapter. Claude, Gemini, OpenCode, and Orca integration require CLI-contract validation and adapter-specific tests; nodes can advertise them but unsupported adapter tasks are rejected by the worker.
- The current UI is polling-based; terminal streaming, budgets/cost-metering, and workflow templates are not implemented.
- Worktree isolation is not an OS sandbox: use a dedicated unprivileged worker account and machine.
- `create_all()` handles initial schema but not future migrations. Introduce Alembic before upgrading schemas in an existing installation.
- Failover is best-effort and subject to local worktree availability and approval policy. Worker credentials can be manually revoked in the DB; automatic key rotation and node lifecycle UI are future work.
- No production deployment or merge has been performed on your existing VPS by this repository.
