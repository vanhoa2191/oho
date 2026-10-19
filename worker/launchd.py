#!/usr/bin/env python3
"""Create a user LaunchAgent plist for the OHO worker.

This module only writes a plist; launchctl operations remain explicit in the
installer so it can report failures without silently unloading an existing job.
"""
import argparse
import os
import plistlib
import tempfile
from pathlib import Path


LABEL = "com.tienganhcungcon.oho-worker"


def build_plist(
    python_path: str,
    worker_script: str,
    config_path: str,
    working_directory: str,
    log_directory: str,
    path_env: str,
) -> dict:
    """Return a minimal, non-secret LaunchAgent configuration."""
    # Preserve the venv interpreter path as written. Resolving symlinks here
    # can point LaunchAgent at Homebrew's base interpreter, which then ignores
    # the venv site-packages and fails to import installed dependencies.
    python = os.path.abspath(os.path.expanduser(python_path))
    script = str(Path(worker_script).expanduser().resolve())
    config = str(Path(config_path).expanduser().resolve())
    workdir = str(Path(working_directory).expanduser().resolve())
    logs = str(Path(log_directory).expanduser().resolve())
    if not Path(python).is_absolute() or not Path(script).is_absolute() or not Path(config).is_absolute():
        raise ValueError("LaunchAgent executable, script and config paths must be absolute")
    if not path_env.strip():
        raise ValueError("PATH must not be empty")
    return {
        "Label": LABEL,
        "ProgramArguments": [python, script, "run", "--config", config],
        "WorkingDirectory": workdir,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(Path(logs) / "oho-worker.log"),
        "StandardErrorPath": str(Path(logs) / "oho-worker-error.log"),
        "EnvironmentVariables": {
            "PATH": path_env,
            "PYTHONUNBUFFERED": "1",
        },
    }


def write_plist(path: str, **kwargs) -> Path:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    document = build_plist(**kwargs)
    fd, temporary = tempfile.mkstemp(prefix=".oho-worker-", suffix=".plist", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(document, stream, fmt=plistlib.FMT_XML, sort_keys=True)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the OHO worker LaunchAgent plist.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    write = subparsers.add_parser("write")
    write.add_argument("--output", required=True)
    write.add_argument("--python", required=True)
    write.add_argument("--script", required=True)
    write.add_argument("--config", required=True)
    write.add_argument("--working-directory", required=True)
    write.add_argument("--log-directory", required=True)
    write.add_argument("--path-env", default=os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"))
    args = parser.parse_args()
    try:
        path = write_plist(
            args.output,
            python_path=args.python,
            worker_script=args.script,
            config_path=args.config,
            working_directory=args.working_directory,
            log_directory=args.log_directory,
            path_env=args.path_env,
        )
    except (OSError, ValueError) as ex:
        parser.exit(1, f"Unable to write OHO LaunchAgent: {ex}\n")
    print(f"LaunchAgent plist written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
