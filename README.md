# OHO — Orchestration Hub

A self-hosted control plane for dispatching coding workflows across macOS and Linux computers, with DAG scheduling and explicit human approval gates.

> **Release maturity: beta / pilot**. The control plane, React dashboard and worker are implemented and have an automated test suite; this is not yet an audited multi-tenant production orchestrator. See [Limitations](#limitations).

## Components

| Component | Technology | Purpose |
|---|---|---|
| Control plane | FastAPI + SQLAlchemy + PostgreSQL | Projects, DAG tasks, leases, approvals, audit events |
| Dashboard | React + TypeScript + Nginx | Node fleet, run history, task review |
| Worker | Python 3.12+ | HTTPS polling, local isolated Git worktrees, headless Codex |
| Reverse proxy | Existing host Caddy | TLS and routing; Cloudflare Access protects the dashboard |

## Authentication modes

- **Local development:** `OHO_AUTH_MODE=bearer` uses an OHO Admin Access Token.
- **Production dashboard:** `OHO_AUTH_MODE=cloudflare_access` verifies the signed Cloudflare Access JWT and exact administrator email. No second dashboard token is needed.
- **Workers:** use the separate restricted hostname `workers.tienganhcungcon.online`. Its gateway exposes only node lifecycle routes, which require the enrollment token or per-node credentials. This removes per-worker Cloudflare Client ID/Secret entry.

See [docs/cloudflare-access.md](docs/cloudflare-access.md) and [docs/worker-gateway.md](docs/worker-gateway.md) before switching or exposing endpoints.

## Quick start (local)

1. Copy `.env.example` to `.env`, replace all placeholder secrets. The password in `OHO_DATABASE_URL` must match `POSTGRES_PASSWORD`.
2. Run `docker compose up -d --build`.
3. In local bearer mode, open `http://127.0.0.1:8788` on the host (or through a trusted SSH tunnel) and enter `OHO_ADMIN_TOKEN`. In production Access mode, use the protected hostname.
4. Create a project and start with a harmless `echo` task.
5. Follow [docs/first-worker.md](docs/first-worker.md) to onboard one worker with a single enrollment token. For macOS background service setup and recovery, see [docs/macos-worker.md](docs/macos-worker.md).

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

## Safety guarantees

- Cloudflare Access mode verifies signed administrator JWTs and checks the exact owner email; worker enrollment and node credentials remain separate.
- Worker gateway only proxies `/api/v1/nodes/*`, rate-limits enrollment and returns 404 for other paths.
- Explicit approval required for agent execution, arbitrary executables and Git write commands.
- Execution never uses `shell=True`; worker enforces a second independent allowlist.
- Per-task Git worktrees avoid modifying the operator's main checkout.
- Task claim uses database row locking, renewable leases and stale-token rejection.
- No automatic merge, push, deployment or privileged system changes.

## Limitations

- Headless Codex is the only wired coding adapter. Claude, Gemini, OpenCode and Orca integration require CLI-contract validation and adapter-specific tests.
- The UI is polling-based; terminal streaming, budgets/cost metering and workflow templates are not implemented.
- Worktree isolation is not an OS sandbox. Use a dedicated unprivileged worker account without production secrets.
- `create_all()` handles initial schema but not future migrations. Introduce Alembic before upgrading schemas in an existing installation.
- Failover is best-effort and subject to local worktree availability and approval policy.


## Safe worktree cleanup

The worker keeps one Git worktree per task so that task branches remain isolated. To inspect old worktrees without deleting anything:

```sh
~/.local/share/oho/venv/bin/python ~/projects/oho/worker/gc.py \
  --config ~/.config/oho/config.json
```

The garbage collector is **dry-run by default** and uses a 24-hour minimum age. It only considers worktrees with a trusted task lock marker, the exact OHO-generated branch pattern, no tracked/untracked/ignored changes, no active lock, and a branch already merged into the project root. Legacy worktrees without lock markers, active tasks, dirty worktrees and unmerged branches are preserved for manual review.

After reviewing the dry-run output, remove eligible worktrees with an explicit confirmation:

```sh
~/.local/share/oho/venv/bin/python ~/projects/oho/worker/gc.py \
  --config ~/.config/oho/config.json --apply
```

Update the worker checkout and restart its service before relying on task locks:

```sh
cd ~/projects/oho
git status --short
git pull --ff-only origin feat/oho-v1-hardened
systemctl --user restart oho-worker
systemctl --user status oho-worker --no-pager
```

Do not use `--yes` for unattended cleanup until the behavior has been observed on real tasks. The script does not run automatically.


## macOS worker service

On macOS, `worker/install.sh` offers to install a user LaunchAgent at `~/Library/LaunchAgents/com.tienganhcungcon.oho-worker.plist`. It starts at user login and restarts on failure. Manage it with `launchctl print gui/$(id -u)/com.tienganhcungcon.oho-worker` or `launchctl kickstart -k gui/$(id -u)/com.tienganhcungcon.oho-worker`. See [docs/macos-worker.md](docs/macos-worker.md) for install, logs, recovery and limitations.
