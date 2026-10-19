# Connect the first OHO worker (Linux or macOS)

This guide assumes the one-time worker gateway is configured. If it is not, complete [worker-gateway.md](worker-gateway.md) on the VPS first. The dashboard remains behind Cloudflare Access; worker machines do not need Cloudflare Client ID/Secret.

## 1. Create the project in OHO

In the dashboard, open **Projects → Add project** and enter:
- Name: `OHO`
- GitHub URL: `https://github.com/vanhoa2191/oho`

## 2. Clone on one Linux or macOS desktop

Do not use the VPS as the first worker. On one Linux or macOS desktop, run:

```bash
mkdir -p ~/projects
git clone --branch feat/oho-v1-hardened https://github.com/vanhoa2191/oho.git ~/projects/oho
cd ~/projects/oho
bash worker/install.sh
```

The installer requires Python 3.12 or 3.13 (3.13 recommended with current dependency pins). The script asks for a node name (press Enter to accept the suggestion), worker API URL (Enter accepts `https://workers.tienganhcungcon.online`), local project path (Enter accepts the checkout), and one OHO enrollment token (hidden input).

It automatically checks Python/Git, writes private configuration, installs dependencies and registers the node. On macOS it offers to install a background LaunchAgent by default; on Linux it offers a foreground smoke test. No Cloudflare Client ID/Secret, manual JSON editing or manual pip setup. For Mac service controls and recovery, see [macOS worker guide](macos-worker.md).

Get the enrollment token from `OHO_ENROLLMENT_TOKEN` in `/opt/oho/.env` through your trusted SSH session. Do not put it in logs, screenshots or chat.

## 3. Confirm the worker is online

Keep the foreground worker running. In OHO, open **Worker nodes** and wait up to 15 seconds for the node to become online.

## 4. Run a harmless smoke test

Choose **Runs → Create run**:
- Project: `OHO`
- Title: `Linux worker smoke test`
- Tasks JSON:

```json
[
  {
    "key": "hello",
    "kind": "command",
    "argv": ["echo", "OHO worker online"]
  }
]
```

Expected: task and run become `succeeded`; task output contains `OHO worker online`. Do not enroll the remaining three machines until this test passes.

## Troubleshooting

- HTTP 404 from the worker hostname root is expected; the gateway only exposes node endpoints.
- HTTP 401 during enrollment usually means the enrollment token is incorrect or the worker is pointed at the dashboard hostname instead of `workers.tienganhcungcon.online`.
- HTTP 403 or a Cloudflare Access login on the worker hostname means a wildcard/overlapping Access application covers it. Keep the dashboard protected; remove unintended Access coverage from the worker hostname rather than creating a broad Bypass policy.
- If the task stays pending, check that the project URL matches the configured repository and the node is online.
- If worktree creation fails, inspect Git status and permissions; do not delete worktrees blindly.

## Service behavior

- Linux: keep the foreground worker for the initial test, then configure the user `systemd` service as described in the deployment conversation/runbook.
- macOS: accept the LaunchAgent option during installation. Check it with `launchctl print gui/$(id -u)/com.tienganhcungcon.oho-worker`; logs are in `~/Library/Logs/oho-worker.log` and `~/Library/Logs/oho-worker-error.log`.

Do not rerun enrollment on an already enrolled machine. Each computer must have a unique node name and its own `~/.config/oho/node.json`.
