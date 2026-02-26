"""Diagnostic checks for GaaS installations.

Verifies prerequisites, configuration, connectivity, and filesystem state.
Designed to be the first thing a user runs when something isn't working.

Usage:
    gaas doctor
"""

import importlib.metadata
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


def _color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _pass(msg: str) -> None:
    print(f"  {GREEN}✓{NC} {msg}" if _color() else f"  PASS {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}!{NC} {msg}" if _color() else f"  WARN {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{NC} {msg}" if _color() else f"  FAIL {msg}")


def _section(msg: str) -> None:
    print(f"\n{BOLD}{msg}{NC}" if _color() else f"\n{msg}")


# ─── Individual checks ────────────────────────────────────────────────────────


def check_python() -> bool:
    """Check Python version is 3.12+."""
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info.micro}"
    if major >= 3 and minor >= 12:
        _pass(f"Python {version}")
        return True
    _fail(f"Python {version} (need 3.12+)")
    return False


def check_uv() -> bool:
    """Check uv is installed."""
    uv = shutil.which("uv")
    if uv:
        result = subprocess.run(
            [uv, "--version"], capture_output=True, text=True
        )
        version = result.stdout.strip() if result.returncode == 0 else "unknown"
        _pass(f"{version}")
        return True
    _fail("uv not found — install: curl -LsSf https://astral.sh/uv/install.sh | sh")
    return False


def check_git() -> bool:
    """Check git is installed and repo is healthy."""
    git = shutil.which("git")
    if not git:
        _fail("git not found")
        return False

    result = subprocess.run(
        [git, "--version"], capture_output=True, text=True
    )
    version = result.stdout.strip() if result.returncode == 0 else "unknown"
    _pass(f"{version}")

    # Check repo state
    if not (PROJECT_ROOT / ".git").is_dir():
        _warn(f"Not a git repository: {PROJECT_ROOT}")
        return True  # git itself is fine

    result = subprocess.run(
        [git, "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        changes = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0
        if changes > 0:
            _warn(f"Repository has {changes} uncommitted change(s)")
        else:
            _pass("Repository clean")
    return True


def check_gh() -> tuple[bool, bool]:
    """Check GitHub CLI. Returns (installed, authenticated)."""
    gh = shutil.which("gh")
    if not gh:
        _warn("GitHub CLI not found (optional, needed for GitHub integration)")
        return False, False

    _pass("GitHub CLI found")

    result = subprocess.run(
        [gh, "auth", "status"], capture_output=True, text=True
    )
    if result.returncode == 0:
        _pass("GitHub CLI authenticated")
        return True, True
    else:
        _warn("GitHub CLI not authenticated (run: gh auth login)")
        return True, False


def check_config() -> bool:
    """Check config.yaml exists and is parseable."""
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        _fail("config.yaml not found — run: gaas setup")
        return False

    _pass(f"config.yaml exists ({config_path})")

    # Try to parse it
    try:
        import yaml

        with open(config_path) as f:
            # Use a safe loader that ignores custom tags
            class _PermissiveLoader(yaml.SafeLoader):
                pass

            # Register handlers for custom tags so parsing doesn't fail
            for tag in ("!secret", "!yolo"):
                _PermissiveLoader.add_constructor(
                    tag, lambda loader, node: loader.construct_scalar(node)
                )
            raw = yaml.load(f, Loader=_PermissiveLoader)

        if not isinstance(raw, dict):
            _fail("config.yaml is not a valid YAML mapping")
            return False

        # Check required top-level keys
        missing = []
        for key in ("llms", "directories"):
            if key not in raw:
                missing.append(key)
        if missing:
            _warn(f"config.yaml missing recommended keys: {', '.join(missing)}")
        else:
            _pass("config.yaml structure looks valid")

        # Count integrations
        integrations = raw.get("integrations", [])
        if integrations:
            _pass(f"{len(integrations)} integration(s) configured")
        else:
            _warn("No integrations configured")

        return True

    except yaml.YAMLError as e:
        _fail(f"config.yaml parse error: {e}")
        return False
    except Exception as e:
        _fail(f"Could not read config.yaml: {e}")
        return False


def check_secrets() -> bool:
    """Check secrets.yaml exists."""
    secrets_path = PROJECT_ROOT / "secrets.yaml"
    if not secrets_path.exists():
        _warn("secrets.yaml not found (needed if config uses !secret references)")
        return False
    _pass("secrets.yaml exists")
    return True


def check_directories() -> bool:
    """Check configured data directories exist and are writable."""
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        return False

    try:
        import yaml

        class _PermissiveLoader(yaml.SafeLoader):
            pass

        for tag in ("!secret", "!yolo"):
            _PermissiveLoader.add_constructor(
                tag, lambda loader, node: loader.construct_scalar(node)
            )

        with open(config_path) as f:
            raw = yaml.load(f, Loader=_PermissiveLoader)

        dirs = raw.get("directories", {})
        if not dirs:
            _warn("No directories configured")
            return False

        all_ok = True
        for name, path_str in dirs.items():
            path = Path(path_str)
            if path.is_dir():
                # Check writable
                if path.stat().st_mode & 0o200:
                    _pass(f"{name}: {path}")
                else:
                    _fail(f"{name}: {path} (not writable)")
                    all_ok = False
            else:
                _warn(f"{name}: {path} (does not exist — will be created on first run)")

        return all_ok

    except Exception:
        _warn("Could not check directories (config parse error)")
        return False


def check_llm_connectivity() -> bool:
    """Check if the default LLM backend is reachable."""
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        return False

    try:
        import yaml
        import urllib.request
        import urllib.error

        class _PermissiveLoader(yaml.SafeLoader):
            pass

        for tag in ("!secret", "!yolo"):
            _PermissiveLoader.add_constructor(
                tag, lambda loader, node: loader.construct_scalar(node)
            )

        with open(config_path) as f:
            raw = yaml.load(f, Loader=_PermissiveLoader)

        llms = raw.get("llms", {})
        default_llm = llms.get("default")
        if not default_llm:
            _warn("No default LLM profile configured")
            return False

        base_url = default_llm.get("base_url", "")
        model = default_llm.get("model", "unknown")

        if not base_url:
            _warn("Default LLM has no base_url")
            return False

        # Try to reach the base URL
        # For Ollama, /api/tags works; for OpenAI-compatible, / or /v1/models
        urls_to_try = [
            f"{base_url.rstrip('/')}/api/tags",  # Ollama
            f"{base_url.rstrip('/')}/v1/models",  # OpenAI-compatible
        ]

        for url in urls_to_try:
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        _pass(f"LLM backend reachable: {base_url} (model: {model})")
                        return True
            except Exception:
                continue

        _warn(f"LLM backend not reachable: {base_url} (model: {model})")
        _warn("  Make sure your LLM server is running")
        return False

    except Exception:
        _warn("Could not check LLM connectivity")
        return False


def check_version() -> bool:
    """Check if we're up to date with the remote."""
    git = shutil.which("git")
    if not git or not (PROJECT_ROOT / ".git").is_dir():
        return True  # Can't check, skip

    # Fetch without changing anything
    subprocess.run(
        [git, "fetch", "--quiet", "origin"],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )

    result = subprocess.run(
        [git, "log", "--oneline", "HEAD..origin/main"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        count = len(result.stdout.strip().splitlines())
        _warn(f"{count} update(s) available — run: gaas update")
        return False

    _pass("Up to date")
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────


def _get_version() -> str:
    """Get the GaaS version from metadata or pyproject.toml fallback."""
    try:
        return importlib.metadata.version("gaas")
    except importlib.metadata.PackageNotFoundError:
        pass
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "unknown"


def run_doctor() -> int:
    """Run all diagnostic checks. Returns 0 if all pass, 1 if any fail."""
    if _color():
        print(f"\n  {BOLD}GaaS Doctor{NC}\n")
    else:
        print("\n  GaaS Doctor\n")

    version = _get_version()
    print(f"  Version: {version}")
    print(f"  Path:    {PROJECT_ROOT}")

    failures = 0
    warnings = 0

    # Prerequisites
    _section("Prerequisites")
    if not check_python():
        failures += 1
    if not check_uv():
        failures += 1
    if not check_git():
        failures += 1
    gh_installed, gh_authed = check_gh()
    if not gh_installed:
        warnings += 1

    # Configuration
    _section("Configuration")
    if not check_config():
        failures += 1
    if not check_secrets():
        warnings += 1

    # Data directories
    _section("Data Directories")
    check_directories()

    # Connectivity
    _section("Connectivity")
    if not check_llm_connectivity():
        warnings += 1

    # Updates
    _section("Updates")
    check_version()

    # Summary
    _section("Summary")
    if failures == 0 and warnings == 0:
        _pass("All checks passed!")
    elif failures == 0:
        _warn(f"All critical checks passed ({warnings} warning(s))")
    else:
        _fail(f"{failures} check(s) failed, {warnings} warning(s)")

    print()
    return 1 if failures > 0 else 0
