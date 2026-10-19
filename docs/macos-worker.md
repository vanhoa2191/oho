# OHO worker on macOS (LaunchAgent)

This guide configures one Mac as an OHO worker. It uses the restricted worker hostname and the same one-time OHO enrollment token as Linux; no Cloudflare Service Token is needed.

## Prerequisites

- macOS with Python 3.12 or 3.13 and Git installed. Python 3.13 is recommended for the currently pinned dependencies.
- Outbound HTTPS access to `workers.tienganhcungcon.online`.
- The one-time worker gateway is already deployed.
- A local Git checkout of the OHO repository.

## Install

On the Mac, open Terminal and run:

```bash
mkdir -p ~/projects
git clone --branch feat/oho-v1-hardened https://github.com/vanhoa2191/oho.git ~/projects/oho
cd ~/projects/oho
bash worker/install.sh
```

The installer asks for a worker name, worker API URL, local project path and OHO enrollment token. Press Enter to accept the suggested name/path/URL. Enrollment-token input is hidden.

On macOS, the installer offers to install and start a user LaunchAgent. Accept the default `Y`. It creates:

- Config: `~/.config/oho/config.json` (mode 0600)
- Node credential: `~/.config/oho/node.json` (mode 0600)
- Python environment: `~/.local/share/oho/venv`
- LaunchAgent: `~/Library/LaunchAgents/com.tienganhcungcon.oho-worker.plist` (mode 0600)
- Standard log: `~/Library/Logs/oho-worker.log`
- Error log: `~/Library/Logs/oho-worker-error.log`

The LaunchAgent preserves the virtualenv interpreter path (it does not resolve the venv Python symlink to the Homebrew base interpreter), uses a clean deterministic PATH, starts at user login, and restarts on process failure. It does not store the enrollment token or any Cloudflare secret in the plist.

## Check status and logs

```bash
launchctl print gui/$(id -u)/com.tienganhcungcon.oho-worker
tail -n 50 ~/Library/Logs/oho-worker.log
tail -n 50 ~/Library/Logs/oho-worker-error.log
```

Then open OHO Dashboard → **Worker nodes** and wait for the node to show online.

## Run the smoke test

Create a run for project `OHO` with this task:

```json
[{"key":"hello","kind":"command","argv":["echo","OHO macOS worker online"]}]
```

Expected: run/task `succeeded`, exit code `0`, output contains `OHO macOS worker online`.

## Manage the service

Restart the worker after updating code (first verify there is no task currently running):

```bash
launchctl kickstart -k gui/$(id -u)/com.tienganhcungcon.oho-worker
```

Stop/unload it:

```bash
launchctl bootout gui/$(id -u)/com.tienganhcungcon.oho-worker
```

Start it again:

```bash
launchctl bootstrap gui/$(id -u) \
  ~/Library/LaunchAgents/com.tienganhcungcon.oho-worker.plist
launchctl kickstart -k gui/$(id -u)/com.tienganhcungcon.oho-worker
```

The installer creates macOS virtual environments with `--copies` and accepts only Python 3.12 or 3.13 for the currently pinned dependencies. The LaunchAgent runs in the logged-in user's GUI session. After a reboot, it starts when that user logs in; it is not a pre-login system daemon.

## Update the worker checkout

Before pulling, confirm the checkout is clean and the branch is correct:

```bash
cd ~/projects/oho
git status --short
git branch --show-current
```

If clean and on `feat/oho-v1-hardened`:

```bash
git pull --ff-only origin feat/oho-v1-hardened
```

Do not rerun `worker/install.sh` on an already enrolled Mac: it deliberately refuses duplicate enrollment. Update the checkout and restart the existing LaunchAgent instead.

## Recovery if enrollment succeeded but LaunchAgent setup failed

Do not run enrollment again and do not delete `node.json`. From `~/projects/oho`, write the plist and load the existing node:

```bash
mkdir -p ~/Library/LaunchAgents ~/Library/Logs

python3 worker/launchd.py write \
  --output "$HOME/Library/LaunchAgents/com.tienganhcungcon.oho-worker.plist" \
  --python "$HOME/.local/share/oho/venv/bin/python" \
  --script "$HOME/projects/oho/worker/agent.py" \
  --config "$HOME/.config/oho/config.json" \
  --working-directory "$HOME/projects/oho" \
  --log-directory "$HOME/Library/Logs" \
  --path-env "$PATH"

plutil -lint "$HOME/Library/LaunchAgents/com.tienganhcungcon.oho-worker.plist"
launchctl bootstrap gui/$(id -u) \
  "$HOME/Library/LaunchAgents/com.tienganhcungcon.oho-worker.plist"
launchctl kickstart -k gui/$(id -u)/com.tienganhcungcon.oho-worker
```

## Security and cleanup

- Never copy the enrollment token into a command argument, shell history, Git, screenshot or chat.
- Each Mac must use a unique worker name. Never copy another machine's `node.json` to a second Mac.
- Before removing a Mac permanently, disable/revoke its node in OHO first. Preserve credentials until revocation is confirmed.
- Use `worker/gc.py` in dry-run mode for stale worktrees. Do not schedule unattended cleanup.
