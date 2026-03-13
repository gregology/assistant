"""Diagnostic checks for Assistant installations.

Verifies prerequisites, configuration, connectivity, and filesystem state.
Designed to be the first thing a user runs when something isn't working.

Usage:
    assistant doctor
"""

import importlib.metadata
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

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
    """Check Python version is 3.11+."""
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info.micro}"
    if major >= 3 and minor >= 11:
        _pass(f"Python {version}")
        return True
    _fail(f"Python {version} (need 3.11+)")
    return False


def check_uv() -> bool:
    """Check uv is installed."""
    uv = shutil.which("uv")
    if uv:
        result = subprocess.run(  # nosec B603
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

    result = subprocess.run(  # nosec B603
        [git, "--version"], capture_output=True, text=True
    )
    version = result.stdout.strip() if result.returncode == 0 else "unknown"
    _pass(f"{version}")

    # Check repo state
    if not (PROJECT_ROOT / ".git").is_dir():
        _warn(f"Not a git repository: {PROJECT_ROOT}")
        return True  # git itself is fine

    result = subprocess.run(  # nosec B603
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

    result = subprocess.run(  # nosec B603
        [gh, "auth", "status"], capture_output=True, text=True
    )
    if result.returncode == 0:
        _pass("GitHub CLI authenticated")
        return True, True
    else:
        _warn("GitHub CLI not authenticated (run: gh auth login)")
        return True, False


def _load_config_yaml() -> dict[str, Any] | None:
    """Load and parse config.yaml with permissive tag handling.

    Returns the parsed dict, or None if the file is empty.
    Raises yaml.YAMLError on parse failure, OSError on read failure.
    Caller is responsible for checking file existence and user-facing messaging.
    """
    import yaml

    config_path = PROJECT_ROOT / "config.yaml"

    class _PermissiveLoader(yaml.SafeLoader):
        pass

    for tag in ("!secret", "!yolo"):
        _PermissiveLoader.add_constructor(
            tag, lambda loader, node: loader.construct_scalar(node)  # type: ignore[arg-type]
        )

    with open(config_path) as f:
        result: dict[str, Any] | None = yaml.load(f, Loader=_PermissiveLoader)  # nosec B506
        return result


def _check_config_structure(raw: dict[str, Any]) -> None:
    """Validate top-level config keys and report integration count."""
    missing = [key for key in ("llms", "directories") if key not in raw]
    if missing:
        _warn(f"config.yaml missing recommended keys: {', '.join(missing)}")
    else:
        _pass("config.yaml structure looks valid")

    integrations = raw.get("integrations", [])
    if integrations:
        _pass(f"{len(integrations)} integration(s) configured")
    else:
        _warn("No integrations configured")


def check_config() -> bool:
    """Check config.yaml exists and is parseable."""
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        _fail("config.yaml not found — run: assistant setup")
        return False

    _pass(f"config.yaml exists ({config_path})")

    import yaml

    try:
        raw = _load_config_yaml()
    except yaml.YAMLError as e:
        _fail(f"config.yaml parse error: {e}")
        return False
    except Exception as e:
        _fail(f"Could not read config.yaml: {e}")
        return False

    if not isinstance(raw, dict):
        _fail("config.yaml is not a valid YAML mapping")
        return False

    _check_config_structure(raw)
    return True


def check_secrets() -> bool:
    """Check secrets.yaml exists."""
    secrets_path = PROJECT_ROOT / "secrets.yaml"
    if not secrets_path.exists():
        _warn("secrets.yaml not found (needed if config uses !secret references)")
        return False
    _pass("secrets.yaml exists")
    return True


def _check_single_directory(name: str, path_str: str) -> bool:
    """Check a single directory exists and is writable. Returns True if OK."""
    path = Path(path_str)
    if not path.is_dir():
        _warn(f"{name}: {path} (does not exist — will be created on first run)")
        return True
    if path.stat().st_mode & 0o200:
        _pass(f"{name}: {path}")
        return True
    _fail(f"{name}: {path} (not writable)")
    return False


def check_directories() -> bool:
    """Check configured data directories exist and are writable."""
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        return False

    try:
        raw = _load_config_yaml()
    except Exception:
        _warn("Could not check directories (config parse error)")
        return False

    if not raw:
        _warn("No directories configured")
        return False

    dirs = raw.get("directories", {})
    if not dirs:
        _warn("No directories configured")
        return False

    return all(_check_single_directory(name, path_str) for name, path_str in dirs.items())


def _extract_default_llm(raw: dict[str, Any]) -> tuple[str, str] | None:
    """Extract base_url and model from the default LLM profile.

    Returns (base_url, model) or None if not configured.
    """
    llms = raw.get("llms", {})
    default_llm = llms.get("default")
    if not default_llm:
        _warn("No default LLM profile configured")
        return None

    base_url = default_llm.get("base_url", "")
    if not base_url:
        _warn("Default LLM has no base_url")
        return None

    return base_url, default_llm.get("model", "unknown")


def _probe_llm_urls(base_url: str, model: str) -> bool:
    """Try to reach the LLM backend at known endpoints. Returns True if reachable."""
    import urllib.request

    urls_to_try = [
        f"{base_url.rstrip('/')}/api/tags",   # Ollama
        f"{base_url.rstrip('/')}/v1/models",   # OpenAI-compatible
    ]
    for url in urls_to_try:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
                if resp.status == 200:
                    _pass(f"LLM backend reachable: {base_url} (model: {model})")
                    return True
        except Exception:  # nosec B112
            continue

    _warn(f"LLM backend not reachable: {base_url} (model: {model})")
    _warn("  Make sure your LLM server is running")
    return False


def check_llm_connectivity() -> bool:
    """Check if the default LLM backend is reachable."""
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        return False

    try:
        raw = _load_config_yaml()
    except Exception:
        _warn("Could not check LLM connectivity")
        return False

    if not raw:
        _warn("Could not check LLM connectivity")
        return False

    llm_info = _extract_default_llm(raw)
    if not llm_info:
        return False

    base_url, model = llm_info
    return _probe_llm_urls(base_url, model)


def check_version() -> bool:
    """Check if we're up to date with the remote."""
    git = shutil.which("git")
    if not git or not (PROJECT_ROOT / ".git").is_dir():
        return True  # Can't check, skip

    # Fetch without changing anything
    subprocess.run(  # nosec B603
        [git, "fetch", "--quiet", "origin"],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )

    result = subprocess.run(  # nosec B603
        [git, "log", "--oneline", "HEAD..origin/main"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        count = len(result.stdout.strip().splitlines())
        _warn(f"{count} update(s) available — run: assistant update")
        return False

    _pass("Up to date")
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────


def _get_version() -> str:
    """Get the Assistant version from metadata or pyproject.toml fallback."""
    try:
        return importlib.metadata.version("assistant")
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
        print(f"\n  {BOLD}Assistant Doctor{NC}\n")
    else:
        print("\n  Assistant Doctor\n")

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
    gh_installed, _gh_authed = check_gh()
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
