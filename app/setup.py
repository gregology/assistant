"""Guided setup wizard for GaaS.

Generates config.yaml and secrets.yaml through interactive prompts.
Reuses the project's config patterns (YAML with !secret references)
and validates the result before writing.

Usage:
    gaas setup                # First-time setup
    gaas setup --reconfigure  # Reconfigure an existing installation
"""

import shutil
import subprocess  # nosec B404
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ─── Colors ───────────────────────────────────────────────────────────────────

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


def _color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _info(msg: str) -> None:
    print(f"{BLUE}::{NC} {msg}" if _color() else f":: {msg}")


def _success(msg: str) -> None:
    print(f"{GREEN}✓{NC} {msg}" if _color() else f"OK {msg}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}!{NC} {msg}" if _color() else f"WARN {msg}")


def _heading(msg: str) -> None:
    print(f"\n{BOLD}── {msg} ──{NC}\n" if _color() else f"\n-- {msg} --\n")


def _prompt(msg: str, default: str = "") -> str:
    """Prompt the user for input with an optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {msg}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return value or default


def _prompt_choice(msg: str, choices: list[str], default: str = "") -> str:
    """Prompt the user to pick from a numbered list."""
    print(f"  {msg}")
    for i, choice in enumerate(choices, 1):
        marker = " (default)" if choice == default else ""
        print(f"    {i}) {choice}{marker}")
    while True:
        raw = _prompt("Enter number", str(choices.index(default) + 1) if default else "")
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        print(f"    Please enter a number between 1 and {len(choices)}")


def _prompt_yn(msg: str, default: bool = True) -> bool:
    """Prompt for yes/no."""
    hint = "Y/n" if default else "y/N"
    raw = _prompt(f"{msg} [{hint}]")
    if not raw:
        return default
    return raw.lower().startswith("y")


# ─── Setup sections ──────────────────────────────────────────────────────────


def setup_llm() -> tuple[dict[str, Any], dict[str, str]]:
    """Configure LLM backends. Returns (config_section, secrets_section)."""
    _heading("LLM Backend")
    _info("GaaS needs an LLM backend for classification tasks.")
    _info("You can use a local Ollama server or an OpenAI-compatible API.\n")

    backend = _prompt_choice(
        "Which LLM backend?",
        ["Ollama (local)", "OpenAI-compatible API", "Skip for now"],
        default="Ollama (local)",
    )

    config: dict[str, Any] = {}
    secrets: dict[str, str] = {}

    if backend == "Skip for now":
        _warn("Skipping LLM setup. You'll need to configure this in config.yaml later.")
        config["default"] = {
            "base_url": "http://localhost:11434",
            "model": "CHANGE_ME",
        }
        return config, secrets

    if backend == "Ollama (local)":
        host = _prompt("Ollama host", "http://localhost:11434")
        model = _prompt("Model name", "llama3.2:latest")
        config["default"] = {"base_url": host, "model": model}

        # Test connection
        _info("Testing Ollama connection...")
        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(f"{host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5):  # nosec B310
                _success("Ollama is reachable")
        except Exception:
            _warn("Could not reach Ollama. Make sure it's running when you start GaaS.")

    elif backend == "OpenAI-compatible API":
        base_url = _prompt("API base URL", "https://api.openai.com")
        model = _prompt("Model name", "gpt-4o-mini")
        token = _prompt("API key")
        if token:
            config["default"] = {
                "base_url": base_url,
                "model": model,
                "token": "!secret llm_api_key",  # nosec B105
            }
            secrets["llm_api_key"] = token
        else:
            config["default"] = {"base_url": base_url, "model": model}

    if _prompt_yn("Add a second LLM profile (e.g. 'fast' model)?", default=False):
        name = _prompt("Profile name", "fast")
        host = _prompt("API base URL", config["default"]["base_url"])
        model = _prompt("Model name")
        profile: dict[str, Any] = {"base_url": host, "model": model}
        if host != config["default"].get("base_url") and "api" in host.lower():
            token = _prompt("API key (leave empty to reuse default)")
            if token:
                secret_key = f"{name}_api_key"
                profile["token"] = f"!secret {secret_key}"
                secrets[secret_key] = token
        config[name] = profile

    return config, secrets


def setup_email() -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Configure email integration. Returns (integrations_list, secrets_section)."""
    _heading("Email Integration")

    if not _prompt_yn("Set up email integration?", default=True):
        return [], {}

    integrations: list[dict[str, Any]] = []
    secrets: dict[str, str] = {}

    name = _prompt("Integration name", "personal")
    server = _prompt("IMAP server", "imap.fastmail.com")
    port = _prompt("IMAP port", "993")
    username = _prompt("Email address")
    password = _prompt("App password (stored in secrets.yaml)")

    secret_key = f"{name}_email_password"
    secrets[secret_key] = password

    schedule = _prompt("Check frequency", "30m")

    integration: dict[str, Any] = {
        "type": "email",
        "name": name,
        "imap_server": server,
        "imap_port": int(port),
        "username": username,
        "password": f"!secret {secret_key}",
        "schedule": {"every": schedule},
        "llm": "default",
        "platforms": {
            "inbox": {
                "limit": 50,
                "classifications": {
                    "human": "is this a personal email written by a human?",
                    "robot": "is this email from an automated system?",
                },
            }
        },
    }
    integrations.append(integration)

    return integrations, secrets


def setup_github() -> list[dict[str, Any]]:
    """Configure GitHub integration. Returns integrations list."""
    _heading("GitHub Integration")

    # Check if gh is available
    gh = shutil.which("gh")
    if not gh:
        _warn("GitHub CLI (gh) not found. Skipping GitHub integration.")
        _info("Install gh from https://cli.github.com/ and re-run: gaas setup --reconfigure")
        return []

    # Check auth status
    result = subprocess.run(  # nosec B603
        [gh, "auth", "status"], capture_output=True, text=True
    )
    if result.returncode != 0:
        _warn("GitHub CLI not authenticated. Skipping GitHub integration.")
        _info("Run 'gh auth login' and then: gaas setup --reconfigure")
        return []

    _success("GitHub CLI authenticated")

    if not _prompt_yn("Set up GitHub integration?", default=True):
        return []

    name = _prompt("Integration name", "my_repos")
    schedule = _prompt("Check frequency", "10m")

    pr_enabled = _prompt_yn("Monitor pull requests?", default=True)
    issues_enabled = _prompt_yn("Monitor issues?", default=True)

    platforms: dict[str, Any] = {}
    if pr_enabled:
        platforms["pull_requests"] = {
            "classifications": {
                "complexity": (
                    "how complex is this PR to review?"
                    " 0 = trivial, 1 = major architectural change"
                ),
                "risk": "how risky is this change? 0 = no risk, 1 = high risk of breaking things",
            }
        }
    if issues_enabled:
        platforms["issues"] = {
            "classifications": {
                "urgency": "how urgently does this issue need attention?",
                "actionable": {
                    "prompt": "can you take a concrete next step on this issue?",
                    "type": "boolean",
                },
            }
        }

    if not platforms:
        return []

    integration: dict[str, Any] = {
        "type": "github",
        "name": name,
        "schedule": {"every": schedule},
        "llm": "default",
        "platforms": platforms,
    }
    return [integration]


def setup_directories() -> dict[str, str]:
    """Configure data directories. Returns directories config."""
    _heading("Data Directories")
    _info("GaaS stores notes, task queue data, and logs on the filesystem.")
    _info("Defaults are inside your home directory.\n")

    default_base = str(Path.home() / ".gaas" / "data")

    base = _prompt("Base data directory", default_base)
    base_path = Path(base)

    dirs = {
        "notes": str(base_path / "notes"),
        "task_queue": str(base_path / "queue"),
        "logs": str(base_path / "logs"),
    }

    # Show the user what we'll create
    print()
    _info("Directories that will be created:")
    for key, path in dirs.items():
        print(f"    {key}: {path}")
    print()

    if not _prompt_yn("Look good?", default=True):
        dirs["notes"] = _prompt("Notes directory", dirs["notes"])
        dirs["task_queue"] = _prompt("Task queue directory", dirs["task_queue"])
        dirs["logs"] = _prompt("Logs directory", dirs["logs"])

    # Create directories
    for _key, path in dirs.items():
        Path(path).mkdir(parents=True, exist_ok=True)
    _success("Directories created")

    return dirs


# ─── Config generation ────────────────────────────────────────────────────────


def _emit_yaml_value(lines: list[str], key: str, value: Any, indent: int) -> None:
    """Emit a single key-value pair with proper YAML formatting."""
    prefix = " " * indent
    if isinstance(value, str) and value.startswith("!secret"):
        secret_name = value.split(" ", 1)[1]
        lines.append(f"{prefix}{key}: !secret {secret_name}")
    elif isinstance(value, dict):
        lines.append(f"{prefix}{key}:")
        for k2, v2 in value.items():
            lines.append(f"{prefix}  {k2}: {v2}")
    else:
        lines.append(f"{prefix}{key}: {value}")


def _emit_llms_section(lines: list[str], llm_config: dict[str, Any]) -> None:
    """Emit the llms: section of config.yaml."""
    lines.append("llms:")
    for profile_name, profile in llm_config.items():
        lines.append(f"  {profile_name}:")
        for key, value in profile.items():
            _emit_yaml_value(lines, key, value, indent=4)


def _emit_integration_field(
    lines: list[str], key: str, value: Any
) -> None:
    """Emit a single field of an integration entry."""
    if key == "type":
        return
    if key == "password" and isinstance(value, str) and value.startswith("!secret"):
        secret_name = value.split(" ", 1)[1]
        lines.append(f"    {key}: !secret {secret_name}")
    elif key == "schedule" and isinstance(value, dict):
        lines.append("    schedule:")
        for sk, sv in value.items():
            lines.append(f"      {sk}: {sv}")
    elif key == "platforms" and isinstance(value, dict):
        lines.append("    platforms:")
        for pname, pconfig in value.items():
            lines.append(f"      {pname}:")
            _write_platform(lines, pconfig, indent=8)
    else:
        lines.append(f"    {key}: {value}")


def _emit_integrations_section(
    lines: list[str], integrations: list[dict[str, Any]]
) -> None:
    """Emit the integrations: section of config.yaml."""
    if not integrations:
        return
    lines.append("")
    lines.append("integrations:")
    for integration in integrations:
        lines.append(f"  - type: {integration['type']}")
        for key, value in integration.items():
            _emit_integration_field(lines, key, value)


def _emit_directories_section(
    lines: list[str], directories: dict[str, str]
) -> None:
    """Emit the directories: section of config.yaml."""
    lines.append("")
    lines.append("directories:")
    for key, path in directories.items():
        lines.append(f"  {key}: {path}")


def _build_config_yaml(
    llm_config: dict[str, Any],
    integrations: list[dict[str, Any]],
    directories: dict[str, str],
) -> str:
    """Build config.yaml content as a string.

    We write YAML by hand rather than using a YAML library to preserve
    the !secret references as literal strings and produce clean, readable output.
    """
    lines: list[str] = []
    _emit_llms_section(lines, llm_config)
    _emit_integrations_section(lines, integrations)
    _emit_directories_section(lines, directories)
    lines.append("")
    return "\n".join(lines)


def _write_platform(lines: list[str], config: dict[str, Any], indent: int) -> None:
    """Recursively write platform config with proper indentation."""
    prefix = " " * indent
    for key, value in config.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            _write_platform(lines, value, indent + 2)
        elif isinstance(value, int):
            lines.append(f"{prefix}{key}: {value}")
        elif isinstance(value, bool):
            lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{prefix}{key}: {value}")


def _build_secrets_yaml(secrets: dict[str, str]) -> str:
    """Build secrets.yaml content."""
    lines = [
        "# GaaS secrets — referenced from config.yaml via !secret <key>",
        "# Keep this file safe. It is gitignored by default.",
        "",
    ]
    for key, value in secrets.items():
        lines.append(f"{key}: {value}")
    lines.append("")
    return "\n".join(lines)


def _backup_file(path: Path) -> Path | None:
    """Back up an existing file with a timestamp. Returns backup path or None."""
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = path.with_suffix(f".bak.{timestamp}")
    shutil.copy2(path, backup)
    return backup


# ─── Main ─────────────────────────────────────────────────────────────────────


def _backup_existing_files(config_path: Path, secrets_path: Path) -> None:
    """Back up config.yaml and secrets.yaml if they exist."""
    if config_path.exists():
        backup = _backup_file(config_path)
        if backup:
            _info(f"Backed up config.yaml to {backup.name}")
    if secrets_path.exists():
        backup = _backup_file(secrets_path)
        if backup:
            _info(f"Backed up secrets.yaml to {backup.name}")


def _print_setup_summary(
    llm_config: dict[str, Any],
    all_integrations: list[dict[str, Any]],
    all_secrets: dict[str, str],
    directories: dict[str, str],
) -> None:
    """Display the setup summary before writing files."""
    _heading("Summary")
    print(f"  LLM profiles:  {', '.join(llm_config.keys()) if llm_config else 'none'}")
    print(f"  Integrations:  {len(all_integrations)}")
    for ig in all_integrations:
        print(f"    - {ig['type']}.{ig['name']}")
    print(f"  Secrets:       {len(all_secrets)} entries")
    print(f"  Data directory: {directories.get('notes', 'N/A').rsplit('/notes', 1)[0]}")
    print()


def _print_next_steps(config_path: Path) -> None:
    """Display next steps after successful setup."""
    print()
    _success("Setup complete!")
    print()
    _info("Next steps:")
    example = PROJECT_ROOT / "example.config.yaml"
    if _color():
        _info(f"  1. Review your config:  {BOLD}cat {config_path}{NC}")
        _info(f"  2. Check your setup:    {BOLD}gaas doctor{NC}")
        _info(f"  3. Start GaaS:          {BOLD}gaas start{NC}")
        _info(f"  Full config reference:  {BOLD}{example}{NC}")
    else:
        _info(f"  1. Review your config:  cat {config_path}")
        _info("  2. Check your setup:    gaas doctor")
        _info("  3. Start GaaS:          gaas start")
        _info(f"  Full config reference:  {example}")
    print()


def run_setup(reconfigure: bool = False) -> int:
    """Run the interactive setup wizard. Returns exit code."""
    config_path = PROJECT_ROOT / "config.yaml"
    secrets_path = PROJECT_ROOT / "secrets.yaml"

    print()
    title = f"  {BOLD}GaaS Setup Wizard{NC}" if _color() else "  GaaS Setup Wizard"
    print(title)
    print()

    if config_path.exists() and not reconfigure:
        _info("config.yaml already exists.")
        if not _prompt_yn("Reconfigure? (existing config will be backed up)", default=False):
            _info("Nothing to do. Run with --reconfigure to force reconfiguration.")
            return 0

    _backup_existing_files(config_path, secrets_path)

    # Run each setup section
    llm_config, llm_secrets = setup_llm()
    email_integrations, email_secrets = setup_email()
    github_integrations = setup_github()
    directories = setup_directories()

    # Merge
    all_integrations = email_integrations + github_integrations
    all_secrets = {**llm_secrets, **email_secrets}

    # Generate files
    config_content = _build_config_yaml(llm_config, all_integrations, directories)
    secrets_content = _build_secrets_yaml(all_secrets)

    _print_setup_summary(llm_config, all_integrations, all_secrets, directories)

    if not _prompt_yn("Write configuration?", default=True):
        _warn("Setup cancelled. No files were written.")
        return 1

    # Write files
    config_path.write_text(config_content)
    _success(f"Written: {config_path}")
    secrets_path.write_text(secrets_content)
    _success(f"Written: {secrets_path}")

    _print_next_steps(config_path)
    return 0
