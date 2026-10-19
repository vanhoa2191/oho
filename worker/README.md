# OHO worker — simplified onboarding

Supported: Linux and macOS, Python 3.12 or 3.13 (3.13 recommended for current pinned dependencies), Git and outbound HTTPS.

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

The script asks for a worker name, API URL, local project path and one OHO enrollment token (hidden input). It checks Python, writes private config, creates a virtual environment, installs dependencies and registers the node. On macOS, it offers to install and start a background LaunchAgent by default; on Linux, it offers to run the worker in the foreground for the first smoke test. See [the macOS LaunchAgent guide](../docs/macos-worker.md).

No Cloudflare Client ID/Secret, manual JSON, manual pip commands or inbound firewall ports are needed.

The enrollment token is stored as `OHO_ENROLLMENT_TOKEN` in `/opt/oho/.env` on the VPS. Retrieve it over a trusted SSH session; never put it in Git, shell command arguments, chat or screenshots.

## Credentials and files

- `~/.config/oho/config.json`: worker config, mode 0600.
- `~/.config/oho/node.json`: individual node credential, mode 0600.
- `~/.local/share/oho/venv`: private Python environment.

After enrollment the master token is cleared from the installer process. Normal worker requests use the individual node token. The installer refuses to enroll again if `~/.config/oho/node.json` already exists; update/restart the existing worker instead of rerunning enrollment. If a node is lost, disable it from OHO and rotate the enrollment token if it may have leaked.

### macOS service management

The installer creates `~/Library/LaunchAgents/com.tienganhcungcon.oho-worker.plist` and logs under `~/Library/Logs/`. Check status with `launchctl print gui/$(id -u)/com.tienganhcungcon.oho-worker`. Restart after a code update with `launchctl kickstart -k gui/$(id -u)/com.tienganhcungcon.oho-worker`. The LaunchAgent starts when the user logs in; it is not a pre-login system daemon.

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


## Coding-agent profiles (pilot)

Command tasks (`echo`, `pytest`, and the restricted Git read-only commands) do not require a model profile. For agent tasks, configure each worker's local `~/.config/oho/config.json` with `agent_profiles`. Profile keys are the model names sent in OHO task JSON; the `model` field inside each profile is the exact provider/CLI model identifier.

Example shape (replace binary paths and model IDs with values verified on that machine):

```json
{
  "agent_profiles": {
    "codex-default": {
      "adapter": "codex",
      "binary": "/absolute/path/to/codex",
      "model": "provider-supported-codex-model"
    },
    "claude-sonnet": {
      "adapter": "claude",
      "binary": "/absolute/path/to/claude",
      "model": "sonnet",
      "max_turns": 5
    },
    "gemini-flash": {
      "adapter": "gemini",
      "binary": "/absolute/path/to/gemini",
      "model": "provider-supported-gemini-model"
    },
    "deepseek-opencode": {
      "adapter": "opencode",
      "binary": "/absolute/path/to/opencode",
      "model": "provider/model-from-opencode-models",
      "credential_env": ["DEEPSEEK_API_KEY"]
    }
  }
}
```

Use `which codex`, `which claude`, `which gemini`, or `which opencode` to find an executable, then prefer its absolute path. A profile is advertised to OHO only when its CLI binary is present. Use `opencode models` to find the exact provider/model ID for OpenCode.

Security behavior in this pilot:
- Every `kind: "agent"` task requires explicit OHO approval.
- Codex uses its workspace-write sandbox.
- Claude runs non-interactively with edit approval mode and the Bash tool explicitly disallowed.
- Gemini uses its sandbox and `auto_edit` mode, never YOLO mode.
- OpenCode receives a restrictive permission map allowing file navigation/editing but denying Bash, web access, delegated tasks and external directories.
- The worker process receives only environment variables explicitly listed in that profile's adapter-specific `credential_env`. Do not put secret values in `config.json`; store them in the machine's secret environment or use the CLI's own login mechanism.

These are initial adapter contracts, not proof that every CLI/model combination works on every machine. Before enabling a profile fleet-wide, run one approved low-risk agent task on that exact host and verify the output, exit code and worktree. The first Claude/Gemini/OpenCode profiles are intentionally more restricted than Codex and may not run tests through shell tools; do not loosen permissions globally just to make a task pass.
