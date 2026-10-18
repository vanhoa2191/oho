#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_URL="https://github.com/vanhoa2191/oho"
DEFAULT_SERVER="https://workers.tienganhcungcon.online"
CONFIG_DIR="${HOME}/.config/oho"
CONFIG_PATH="${CONFIG_DIR}/config.json"
VENV_DIR="${HOME}/.local/share/oho/venv"
WORKER_SCRIPT="${ROOT}/worker/agent.py"
fail() { printf 'OHO setup failed: %s\n' "$*" >&2; exit 1; }
cleanup() { unset OHO_ENROLLMENT_TOKEN 2>/dev/null || true; }
trap cleanup EXIT
command -v git >/dev/null 2>&1 || fail "Git is required."
command -v python3 >/dev/null 2>&1 || fail "Python 3.12+ is required."
[[ -f "$WORKER_SCRIPT" ]] || fail "Run this script from an OHO repository checkout."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' || fail "Install Python 3.12 or newer and retry."
case "$(uname -s)" in
  Linux) PLATFORM_NAME="linux"; DEFAULT_TAGS="linux,general" ;;
  Darwin) PLATFORM_NAME="macos"; DEFAULT_TAGS="macos,general" ;;
  *) fail "Supported worker operating systems are Linux and macOS." ;;
esac
DEFAULT_NAME="$(hostname -s 2>/dev/null || hostname)"
DEFAULT_NAME="$(printf '%s' "$DEFAULT_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | cut -c1-48)"
printf '\nOHO worker setup — one enrollment token; no Cloudflare service token.\n'
read -r -p "Worker name [$DEFAULT_NAME]: " NODE_NAME
NODE_NAME="${NODE_NAME:-$DEFAULT_NAME}"
[[ "$NODE_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{1,62}$ ]] || fail "Invalid worker name."
read -r -p "Worker API URL [$DEFAULT_SERVER]: " SERVER_URL
SERVER_URL="${SERVER_URL:-$DEFAULT_SERVER}"
[[ "$SERVER_URL" == https://* ]] || fail "Worker endpoint must use HTTPS."
read -r -p "Local OHO repository path [$ROOT]: " PROJECT_ROOT
PROJECT_ROOT="${PROJECT_ROOT:-$ROOT}"
PROJECT_ROOT="$(cd -- "$PROJECT_ROOT" 2>/dev/null && pwd)" || fail "Project path does not exist."
[[ -d "$PROJECT_ROOT/.git" || -f "$PROJECT_ROOT/.git" ]] || fail "Project path is not a Git checkout."
printf '\nEnter the OHO enrollment token from the VPS. Input is hidden.\n'
read -r -s -p "OHO enrollment token: " OHO_ENROLLMENT_TOKEN; printf '\n'
[[ "${#OHO_ENROLLMENT_TOKEN}" -ge 24 ]] || fail "Enrollment token is missing or too short."
export OHO_SERVER_URL="$SERVER_URL" OHO_NODE_NAME="$NODE_NAME" OHO_PLATFORM_NAME="$PLATFORM_NAME" OHO_PROJECT_ROOT="$PROJECT_ROOT" OHO_REPO_URL="$REPO_URL" OHO_DEFAULT_TAGS="$DEFAULT_TAGS"
mkdir -p "$CONFIG_DIR" "$(dirname "$VENV_DIR")"; chmod 700 "$CONFIG_DIR"
python3 - <<'PY'
import json, os
from pathlib import Path
config = {
    "server_url": os.environ["OHO_SERVER_URL"].rstrip("/"),
    "name": os.environ["OHO_NODE_NAME"],
    "platform": os.environ["OHO_PLATFORM_NAME"],
    "tags": os.environ["OHO_DEFAULT_TAGS"].split(","),
    "models": [],
    "project_roots": {os.environ["OHO_REPO_URL"]: os.environ["OHO_PROJECT_ROOT"]},
    "allowed_commands": ["echo", "pytest", "git"],
    "agent_binaries": {},
    "poll_seconds": 5
}
path = Path.home() / ".config" / "oho" / "config.json"
path.write_text(json.dumps(config, indent=2) + "\n")
path.chmod(0o600)
print("Private worker configuration saved.")
PY
if [[ ! -x "$VENV_DIR/bin/python" ]]; then python3 -m venv "$VENV_DIR"; fi
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet -r "$ROOT/worker/requirements.txt"
printf '\nRegistering worker...\n'
export OHO_ENROLLMENT_TOKEN
"$VENV_DIR/bin/python" "$WORKER_SCRIPT" enroll --config "$CONFIG_PATH"
unset OHO_ENROLLMENT_TOKEN
printf '\nWorker registered. Node credentials are stored with mode 0600.\n'
read -r -p "Start worker now for the smoke test? [Y/n]: " START_NOW
if [[ ! "$START_NOW" =~ ^[Nn]$ ]]; then exec "$VENV_DIR/bin/python" "$WORKER_SCRIPT" run --config "$CONFIG_PATH"; fi
printf '\nTo start later:\n  %q %q --config %q\n' "$VENV_DIR/bin/python" "$WORKER_SCRIPT" "$CONFIG_PATH"
