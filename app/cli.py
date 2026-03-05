"""GaaS CLI — the main entry point for the `gaas` command.

Usage (via wrapper):
    gaas start [--dev] [--expose] [--port N]
    gaas setup [--reconfigure]
    gaas update [--version TAG]
    gaas doctor
    gaas version
    gaas status
    gaas logs [--tail N]

Usage (direct):
    uv run python -m app.cli <subcommand> [args]
"""

import argparse
import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ─── Colors ───────────────────────────────────────────────────────────────────

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _info(msg: str) -> None:
    if _supports_color():
        print(f"{BLUE}::{NC} {msg}")
    else:
        print(f":: {msg}")


def _success(msg: str) -> None:
    if _supports_color():
        print(f"{GREEN}✓{NC} {msg}")
    else:
        print(f"OK {msg}")


def _warn(msg: str) -> None:
    if _supports_color():
        print(f"{YELLOW}!{NC} {msg}")
    else:
        print(f"WARN {msg}")


def _error(msg: str) -> None:
    if _supports_color():
        print(f"{RED}✗{NC} {msg}", file=sys.stderr)
    else:
        print(f"ERROR {msg}", file=sys.stderr)


# ─── Subcommands ──────────────────────────────────────────────────────────────


def cmd_start(args: argparse.Namespace) -> int:
    """Start the GaaS server and worker via the supervisor."""
    # Build argv as the supervisor expects it
    supervisor_args = []
    if args.dev:
        supervisor_args.append("--dev")
    if args.expose:
        supervisor_args.append("--expose")
    if args.port != 6767:
        supervisor_args.extend(["--port", str(args.port)])

    # Replace this process with the supervisor
    sys.argv = ["supervisor"] + supervisor_args
    from app.supervisor import main as supervisor_main

    supervisor_main()
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Run the guided setup wizard."""
    from app.setup import run_setup

    return run_setup(reconfigure=args.reconfigure)


def cmd_update(args: argparse.Namespace) -> int:
    """Update GaaS to the latest version."""
    git = shutil.which("git")
    if not git:
        _error("git not found in PATH")
        return 1

    if not (PROJECT_ROOT / ".git").is_dir():
        _error(f"Not a git repository: {PROJECT_ROOT}")
        _error("GaaS must be installed via git clone for updates to work.")
        return 1

    branch = os.environ.get("GAAS_BRANCH", "main")

    _info(f"Updating GaaS at {PROJECT_ROOT}...")

    # Fetch latest changes
    _info("Fetching latest changes...")
    result = subprocess.run(
        [git, "fetch", "origin", branch],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _error(f"git fetch failed: {result.stderr.strip()}")
        return 1

    # Show what's new
    result = subprocess.run(
        [git, "log", "--oneline", f"HEAD..origin/{branch}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    new_commits = result.stdout.strip()
    if not new_commits:
        _success("Already up to date!")
        return 0

    _info("New changes:")
    for line in new_commits.splitlines():
        print(f"  {line}")
    print()

    # Fast-forward to latest
    result = subprocess.run(
        [git, "merge", "--ff-only", f"origin/{branch}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _error("Fast-forward merge failed. You may have local changes.")
        _error(f"  {result.stderr.strip()}")
        _error(f"Resolve manually in {PROJECT_ROOT}")
        return 1
    _success("Code updated")

    # Re-sync dependencies
    _info("Syncing dependencies...")
    uv = shutil.which("uv")
    if not uv:
        _error("uv not found. Run: curl -LsSf https://astral.sh/uv/install.sh | sh")
        return 1

    result = subprocess.run(
        [uv, "sync", "--quiet"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _error(f"uv sync failed: {result.stderr.strip()}")
        return 1
    _success("Dependencies synced")

    # Regenerate wrapper script in case it changed
    _regenerate_wrapper()

    print()
    _success("GaaS updated successfully!")
    _info("Restart GaaS if it's running: gaas start")
    return 0


def _regenerate_wrapper() -> None:
    """Regenerate the wrapper script to pick up any path changes."""
    bin_dir = os.environ.get("GAAS_BIN_DIR", str(Path.home() / ".local" / "bin"))
    wrapper = Path(bin_dir) / "gaas"
    if not wrapper.exists():
        return  # No wrapper to regenerate

    gaas_home = os.environ.get("GAAS_HOME", str(PROJECT_ROOT))
    content = f"""#!/bin/bash
# GaaS CLI wrapper — generated by install.sh
# Re-run the installer to regenerate, or edit GAAS_HOME below.
set -euo pipefail
GAAS_HOME="${{GAAS_HOME:-{gaas_home}}}"
if [ ! -d "$GAAS_HOME" ]; then
    echo "Error: GaaS not found at $GAAS_HOME" >&2
    echo "Re-run the installer or set GAAS_HOME to the correct path." >&2
    exit 1
fi
export PYTHONPATH="$GAAS_HOME${{PYTHONPATH:+:$PYTHONPATH}}"
exec uv run --project "$GAAS_HOME" python -m app.cli "$@"
"""
    wrapper.write_text(content)
    wrapper.chmod(0o755)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run diagnostic checks."""
    from app.doctor import run_doctor

    return run_doctor()


def _get_version() -> str:
    """Get the GaaS version from metadata or pyproject.toml fallback."""
    try:
        return importlib.metadata.version("gaas")
    except importlib.metadata.PackageNotFoundError:
        pass
    # Fallback: read from pyproject.toml
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.strip().startswith("version"):
                # version = "0.1.0"
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "unknown"


def cmd_version(args: argparse.Namespace) -> int:
    """Print version information."""
    version = _get_version()

    # Get git info
    git_hash = "unknown"
    git_branch = "unknown"
    git = shutil.which("git")
    if git and (PROJECT_ROOT / ".git").is_dir():
        result = subprocess.run(
            [git, "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            git_hash = result.stdout.strip()
        result = subprocess.run(
            [git, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            git_branch = result.stdout.strip()

    print(f"GaaS v{version}")
    print(f"  Branch: {git_branch}")
    print(f"  Commit: {git_hash}")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  Path:   {PROJECT_ROOT}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Check if GaaS is running."""
    import urllib.request
    import urllib.error

    port = 6767
    url = f"http://127.0.0.1:{port}/"

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                _success(f"GaaS is running on port {port}")
                return 0
    except (urllib.error.URLError, ConnectionRefusedError, OSError):
        pass

    _warn(f"GaaS is not running (no response on port {port})")
    _info("Start it with: gaas start")
    return 1


def cmd_logs(args: argparse.Namespace) -> int:
    """Show recent human-readable logs."""
    try:
        from app.config import config

        logs_dir = Path(config.directories.logs)
    except Exception:
        _error("Could not load config. Run 'gaas setup' first.")
        return 1

    if not logs_dir.is_dir():
        _warn(f"Logs directory does not exist: {logs_dir}")
        return 1

    # Find the most recent log file
    log_files = sorted(logs_dir.glob("*.md"), reverse=True)
    if not log_files:
        _info("No log files found yet.")
        return 0

    latest = log_files[0]
    _info(f"Showing: {latest.name}")
    print()

    lines = latest.read_text().splitlines()
    tail = args.tail
    if tail and len(lines) > tail:
        lines = lines[-tail:]

    for line in lines:
        print(line)

    return 0


# ─── Argument parsing ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaas",
        description="GaaS (Greg as a Service) — AI-powered personal assistant",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # gaas start
    p_start = subparsers.add_parser("start", help="Start GaaS server and worker")
    p_start.add_argument("--dev", action="store_true", help="Enable auto-reload")
    p_start.add_argument(
        "--expose",
        action="store_true",
        help="Allow external connections (bind 0.0.0.0)",
    )
    p_start.add_argument(
        "--port", type=int, default=6767, help="Port number (default: 6767)"
    )
    p_start.set_defaults(func=cmd_start)

    # gaas setup
    p_setup = subparsers.add_parser("setup", help="Run guided setup wizard")
    p_setup.add_argument(
        "--reconfigure",
        action="store_true",
        help="Reconfigure an existing installation",
    )
    p_setup.set_defaults(func=cmd_setup)

    # gaas update
    p_update = subparsers.add_parser("update", help="Update GaaS to latest version")
    p_update.set_defaults(func=cmd_update)

    # gaas doctor
    p_doctor = subparsers.add_parser(
        "doctor", help="Run diagnostic checks on your installation"
    )
    p_doctor.set_defaults(func=cmd_doctor)

    # gaas version
    p_version = subparsers.add_parser("version", help="Show version information")
    p_version.set_defaults(func=cmd_version)

    # gaas status
    p_status = subparsers.add_parser("status", help="Check if GaaS is running")
    p_status.set_defaults(func=cmd_status)

    # gaas logs
    p_logs = subparsers.add_parser("logs", help="Show recent human-readable logs")
    p_logs.add_argument(
        "--tail", type=int, default=None, help="Show last N lines"
    )
    p_logs.set_defaults(func=cmd_logs)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
