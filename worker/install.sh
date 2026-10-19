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
[[ -f "$WORKER_SCRIPT" ]] || fail "Run this script from an OHO repository checkout."

# Prefer Python 3.13, then 3.12, then python3 if it is in the supported range.
# This avoids accidentally selecting a newer Homebrew Python with no compatible
# pydantic-core wheel for the currently pinned worker dependencies.
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    candidate_path="$(command -v "$candidate")"
    if "$candidate_path" -c 'import sys; sys.exit(0 if (3,12) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then
      PYTHON_BIN="$candidate_path"
      break
    fi
  fi
done
[[ -n "$PYTHON_BIN" ]] || fail "This worker dependency set supports Python 3.12 or 3.13. Install Python 3.13 (recommended) and retry."
printf 'Using Python: %s\n' "$("$PYTHON_BIN" --version 2>&1)"
case "$(uname -s)" in
  Linux) PLATFORM_NAME="linux"; DEFAULT_TAGS="linux,general" ;;
  Darwin) PLATFORM_NAME="macos"; DEFAULT_TAGS="macos,general" ;;
  *) fail "Supported worker operating systems are Linux and macOS." ;;
esac

# Never enroll the same local node twice. Its node credential is shared by all
# processes for this account, so an existing credential requires an upgrade,
# not another enrollment.
if [[ -f "$CONFIG_DIR/node.json" ]]; then
  fail "This account already has an enrolled OHO node at $CONFIG_DIR/node.json. Do not enroll again; update the checkout and restart the existing worker service."
fi
if [[ "$PLATFORM_NAME" == "macos" ]]; then
  SERVICE_TARGET="gui/$(id -u)/com.tienganhcungcon.oho-worker"
  if launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
    fail "The OHO LaunchAgent is already loaded. Do not create a second worker; inspect it with launchctl print $SERVICE_TARGET."
  fi
fi
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
"$PYTHON_BIN" - <<'PY'
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
    "agent_profiles": {},
    "poll_seconds": 5
}
path = Path.home() / ".config" / "oho" / "config.json"
path.write_text(json.dumps(config, indent=2) + "\n")
path.chmod(0o600)
print("Private worker configuration saved.")
PY
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if [[ "$PLATFORM_NAME" == "macos" ]]; then
    # Homebrew interpreter paths can change across upgrades; copies also avoid
    # resolving the venv's python symlink back to the base interpreter.
    "$PYTHON_BIN" -m venv --copies "$VENV_DIR"
  else
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
fi
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet -r "$ROOT/worker/requirements.txt"
printf '\nRegistering worker...\n'
export OHO_ENROLLMENT_TOKEN
"$VENV_DIR/bin/python" "$WORKER_SCRIPT" enroll --config "$CONFIG_PATH"
unset OHO_ENROLLMENT_TOKEN
printf '\nWorker registered. Node credentials are stored with mode 0600.\n'

if [[ "$PLATFORM_NAME" == "macos" ]]; then
  read -r -p "Install and start a background macOS LaunchAgent now? [Y/n]: " INSTALL_SERVICE
  if [[ ! "$INSTALL_SERVICE" =~ ^[Nn]$ ]]; then
    LABEL="com.tienganhcungcon.oho-worker"
    SERVICE_TARGET="gui/$(id -u)/$LABEL"
    LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
    LOG_DIR="$HOME/Library/Logs"
    PLIST_PATH="$LAUNCH_AGENTS/$LABEL.plist"
    mkdir -p "$LAUNCH_AGENTS" "$LOG_DIR"
    if launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
      fail "LaunchAgent is already loaded; refusing to start a duplicate."
    fi
    "$PYTHON_BIN" "$ROOT/worker/launchd.py" write \
      --output "$PLIST_PATH" \
      --python "$VENV_DIR/bin/python" \
      --script "$WORKER_SCRIPT" \
      --config "$CONFIG_PATH" \
      --working-directory "$ROOT" \
      --log-directory "$LOG_DIR" \
      --path-env "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"
    plutil -lint "$PLIST_PATH" >/dev/null
    launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
    launchctl kickstart "$SERVICE_TARGET"
    sleep 2
    if ! launchctl print "$SERVICE_TARGET" 2>/dev/null | grep -q 'state = running'; then
      echo "LaunchAgent did not reach running state. Inspect logs:"
      echo "  $LOG_DIR/oho-worker-error.log"
      exit 1
    fi
    printf '\nOHO LaunchAgent is running. It will restart on failure and start at user login.\n'
    printf 'Status: launchctl print %q\n' "$SERVICE_TARGET"
    printf 'Logs:   %q and %q\n' "$LOG_DIR/oho-worker.log" "$LOG_DIR/oho-worker-error.log"
    printf 'Stop:   launchctl bootout %q\n' "$SERVICE_TARGET"
    exit 0
  fi
fi

read -r -p "Start worker in this terminal for the smoke test? [Y/n]: " START_NOW
if [[ ! "$START_NOW" =~ ^[Nn]$ ]]; then exec "$VENV_DIR/bin/python" "$WORKER_SCRIPT" run --config "$CONFIG_PATH"; fi
printf '\nTo start later:\n  %q %q --config %q\n' "$VENV_DIR/bin/python" "$WORKER_SCRIPT" "$CONFIG_PATH"
