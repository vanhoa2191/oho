# Connect the first OHO worker (Linux desktop)

This runbook connects one Linux desktop to the OHO control plane at `https://oho.tienganhcungcon.online`. Do not use the VPS itself as the first worker. Do not enroll all four machines until the smoke test passes.

## 0. Preconditions

- OHO dashboard is reachable through Cloudflare Access and admin APIs verify the Access JWT.
- The dashboard has a project named `OHO` with repository URL `https://github.com/vanhoa2191/oho`. If not, use **Projects → Add project** first.
- You have terminal access to one Linux desktop.
- The current worker code is on `feat/oho-v1-hardened`. Only the `echo` smoke test is enabled initially.

## 1. Create a Cloudflare Access service token

On Cloudflare Dashboard:

1. Open **Zero Trust → Access controls → Service credentials → Service Tokens**.
2. Select **Create Service Token**.
3. Name it `oho-worker-linux-1`.
4. Choose a pilot expiry (90 days is a reasonable starting point; note the expiry so it can be rotated before then).
5. Generate it and securely save the Client ID and Client Secret. Cloudflare displays the secret only once.
6. Open **Access controls → Applications → OHO Dashboard → Policies**.
7. Add a policy with:
   - Name: `OHO Worker Linux 1`
   - Action: **Service Auth**
   - Include selector: **Service Token**
   - Value: the token `oho-worker-linux-1`
8. Keep the existing human **Allow** policy restricted to the administrator email. Do not create a broad **Bypass** policy.

Service token requests must include both `CF-Access-Client-Id` and `CF-Access-Client-Secret`. The worker code reads them from environment variables and never stores them in the project config.

## 2. Prepare the Linux desktop

Install Python 3.12+, Git and a virtual-environment-capable Python. Then clone the repo to a dedicated local checkout. If the repository is private, use your existing GitHub CLI/SSH authentication; do not put a PAT in the URL.

```bash
mkdir -p ~/projects
git clone --branch feat/oho-v1-hardened https://github.com/vanhoa2191/oho.git ~/projects/oho
cd ~/projects/oho
git status --short
```

If the checkout already exists, confirm it is a Git repository and has no uncommitted changes before using it as the worker's project root. Never point the worker at a directory containing unrelated projects or production secrets.

## 3. Create worker configuration

From the checkout:

```bash
cd ~/projects/oho
mkdir -p worker
python3 - <<'PY'
import json
import os
from pathlib import Path

home = str(Path.home())
cfg = {
    "server_url": "https://oho.tienganhcungcon.online",
    "name": "linux-worker-1",
    "platform": "linux",
    "tags": ["linux", "general"],
    "models": [],
    "project_roots": {
        "https://github.com/vanhoa2191/oho": os.path.join(home, "projects", "oho")
    },
    "allowed_commands": ["echo", "pytest", "git"],
    "agent_binaries": {},
    "poll_seconds": 5
}
path = Path("worker/config.json")
path.write_text(json.dumps(cfg, indent=2) + "\n")
path.chmod(0o600)
print("Created worker/config.json; no credentials were written.")
PY

python3 -m venv worker/.venv
worker/.venv/bin/python -m pip install -r worker/requirements.txt
```

The config file contains local paths and should not be committed. The repository's `.gitignore` excludes `worker/config.json`.

## 4. Enroll the worker

You need two independent credentials:

- **Cloudflare Access service token** (Client ID and Client Secret), to pass the Cloudflare edge.
- **OHO enrollment token**, from `OHO_ENROLLMENT_TOKEN` in `/opt/oho/.env`, to enroll the node in OHO.

On the Linux desktop, load the Cloudflare values without echoing them or putting them in shell history:

```bash
read -r -p "Cloudflare Client ID: " CF_ACCESS_CLIENT_ID
read -r -s -p "Cloudflare Client Secret: " CF_ACCESS_CLIENT_SECRET; echo
read -r -s -p "OHO enrollment token: " OHO_ENROLLMENT_TOKEN; echo
export CF_ACCESS_CLIENT_ID CF_ACCESS_CLIENT_SECRET OHO_ENROLLMENT_TOKEN
```

Copy each value securely from your password manager. If you need to retrieve the OHO enrollment token from the VPS, read it locally over your trusted SSH session and do not paste it into chat, tickets or screenshots.

Enroll:

```bash
cd ~/projects/oho
worker/.venv/bin/python worker/agent.py enroll --config worker/config.json
unset OHO_ENROLLMENT_TOKEN
```

Expected output confirms node name `linux-worker-1` and its node ID. The worker's individual credential is stored at `~/.config/oho/node.json` with restrictive permissions. Keep `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` exported only for the smoke test; do not print them.

## 5. Start the worker in the foreground

In the same terminal (with both Cloudflare Access variables still exported):

```bash
cd ~/projects/oho
worker/.venv/bin/python worker/agent.py run --config worker/config.json
```

Keep this process running for the initial test. It polls the control plane over outbound HTTPS; no inbound firewall ports are needed.

In OHO Dashboard, open **Worker nodes**. The node should show online within about 15 seconds.

## 6. Run a harmless smoke test

In OHO Dashboard, choose **Runs → Create run**:

- Project: `OHO`
- Run title: `Linux worker smoke test`
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

Do not use the default `pytest` task for the first test; it is treated as elevated and requires approval. The `echo` task is intended to be low risk.

Expected result:
- Task transitions `pending → leased/running → succeeded`.
- Run status becomes `succeeded`.
- Task output contains `OHO worker online`.
- Worker remains online.

## 7. Troubleshooting

- **Cloudflare 302/403/401 before reaching OHO:** check the Service Auth policy is attached to the correct Self-hosted app and selects the exact service token. Do not disable Access.
- **OHO returns invalid enrollment token:** verify the OHO enrollment token from `/opt/oho/.env`; do not use the old Admin Access Token.
- **Worker is offline:** verify the process is running, the public hostname resolves, and both Cloudflare service-token variables are present.
- **Task stays pending:** inspect worker logs in its terminal, check required tags and verify the project URL/path mapping.
- **Git worktree creation fails:** check `git status`, file permissions and whether a branch with the generated `oho/<run>-<task>` name already exists. Do not delete existing worktrees blindly.

## 8. Stop and cleanup

Stop the foreground worker with Ctrl+C. It can be supervised by systemd only after the smoke test passes. Remove the service token environment variables from the shell with:

```bash
unset CF_ACCESS_CLIENT_ID CF_ACCESS_CLIENT_SECRET OHO_ENROLLMENT_TOKEN
```

Do not enroll the other three computers yet. First verify the node can be revoked and the service token rotated independently.
