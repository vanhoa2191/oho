# OHO worker — simplified onboarding

Supported: Linux and macOS, Python 3.12+, Git and outbound HTTPS.

## One-time server setup

The human dashboard stays behind Cloudflare Access at `oho.tienganhcungcon.online`. Workers use a dedicated hostname `workers.tienganhcungcon.online` with a restricted gateway that forwards only node lifecycle routes. The gateway does not expose the dashboard or general admin API; enrollment requires the OHO enrollment token and each registered node uses its own credential.

Follow [docs/worker-gateway.md](../docs/worker-gateway.md) for the one-time DNS, gateway and Caddy setup. Do not create per-worker Cloudflare Service Tokens and do not add a broad Access Bypass policy.

## Install a worker

Clone this repository on the workstation, or use an existing checkout:

```bash
mkdir -p ~/projects
git clone --branch feat/oho-v1-hardened https://github.com/vanhoa2191/oho.git ~/projects/oho
cd ~/projects/oho
bash worker/install.sh
```

The script asks for a worker name, API URL, local project path and one OHO enrollment token (hidden input). It checks Python, writes private config, creates a virtual environment, installs dependencies, registers the node and offers to start the worker.

No Cloudflare Client ID/Secret, manual JSON, manual pip commands or inbound firewall ports are needed.

The enrollment token is stored as `OHO_ENROLLMENT_TOKEN` in `/opt/oho/.env` on the VPS. Retrieve it over a trusted SSH session; never put it in Git, shell command arguments, chat or screenshots.

## Credentials and files

- `~/.config/oho/config.json`: worker config, mode 0600.
- `~/.config/oho/node.json`: individual node credential, mode 0600.
- `~/.local/share/oho/venv`: private Python environment.

After enrollment the master token is cleared from the installer process. Normal worker requests use the individual node token. If a node is lost, disable it from OHO and rotate the enrollment token if it may have leaked.

## First smoke test

In the dashboard, create a run for project `OHO` with task:

```json
[{"key":"hello","kind":"command","argv":["echo","OHO worker online"]}]
```

Expected: task and run become `succeeded`, and task output contains `OHO worker online`. Do not add the remaining machines until this test passes.

## Execution boundaries

Only `echo`, `pytest` and a narrow read-only Git allowlist are available. Arbitrary shell, Git push/merge, deployment and privileged operations remain blocked. Worktree isolation is not an OS sandbox; use a low-privilege account without production secrets.


## Safe worktree cleanup

Every task uses an isolated worktree. After updating the checkout to the current worker code and restarting the worker service, inspect cleanup candidates:

```bash
~/.local/share/oho/venv/bin/python ~/projects/oho/worker/gc.py \
  --config ~/.config/oho/config.json
```

This is dry-run by default and waits at least 24 hours. Only clean worktrees with an unlocked task lock, an OHO-generated branch, no tracked/untracked/ignored changes, and a branch already merged into the configured project root are eligible. Active tasks, dirty worktrees, unmerged code and older worktrees that lack lock markers are skipped.

After reviewing the output, use `--apply` to request interactive confirmation and remove eligible worktrees/merged branches. Do not schedule unattended cleanup. Do not delete skipped worktrees manually without checking task state and preserving any code changes.
