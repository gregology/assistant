"""Guided setup wizard for Assistant.

Generates config.yaml and secrets.yaml through interactive prompts.
Reuses the project's config patterns (YAML with !secret references)
and validates the result before writing.

Usage:
    assistant setup                # First-time setup
    assistant setup --reconfigure  # Reconfigure an existing installation
"""

import shutil
import subprocess  # nosec B404
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

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
    _info("Assistant needs an LLM backend for classification tasks.")
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
            _warn("Could not reach Ollama. Make sure it's running when you start Assistant.")

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
        _info("Install gh from https://cli.github.com/ and re-run: assistant setup --reconfigure")
        return []

    # Check auth status
    result = subprocess.run(  # nosec B603
        [gh, "auth", "status"], capture_output=True, text=True
    )
    if result.returncode != 0:
        _warn("GitHub CLI not authenticated. Skipping GitHub integration.")
        _info("Run 'gh auth login' and then: assistant setup --reconfigure")
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
    _info("Assistant stores notes, task queue data, and logs on the filesystem.")
    _info("Defaults are inside your home directory.\n")

    default_base = str(Path.home() / ".assistant" / "data")

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


def _yaml_entry(key: str, value: Any, indent: int = 0) -> str:
    """Render a YAML key-value entry, recursing into dicts.

    Handles !secret references, booleans, and nested dicts.  Registered as
    a Jinja2 global so templates can call ``{{ yaml_entry(k, v, 4) }}``.
    """
    prefix = " " * indent
    if isinstance(value, dict):
        lines = [f"{prefix}{key}:"]
        for k, v in value.items():
            lines.append(_yaml_entry(k, v, indent + 2))
        return "\n".join(lines)
    if isinstance(value, str) and value.startswith("!secret"):
        return f"{prefix}{key}: !secret {value.split(' ', 1)[1]}"
    if isinstance(value, bool):
        return f"{prefix}{key}: {'true' if value else 'false'}"
    return f"{prefix}{key}: {value}"


_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(  # nosec B701 — plaintext templates, not HTML
    loader=FileSystemLoader(_TEMPLATES_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_jinja_env.globals["yaml_entry"] = _yaml_entry


def _build_config_yaml(
    llm_config: dict[str, Any],
    integrations: list[dict[str, Any]],
    directories: dict[str, str],
) -> str:
    """Build config.yaml content from a Jinja2 template.

    Uses the yaml_entry() helper for nested dicts and !secret references,
    producing clean, readable YAML with proper indentation.
    """
    template = _jinja_env.get_template("config.yaml.jinja")
    return template.render(
        llm_config=llm_config,
        integrations=integrations,
        directories=directories,
    )


def _build_secrets_yaml(secrets: dict[str, str]) -> str:
    """Build secrets.yaml content from a Jinja2 template."""
    template = _jinja_env.get_template("secrets.yaml.jinja")
    return template.render(secrets=secrets)


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
        _info(f"  2. Check your setup:    {BOLD}assistant doctor{NC}")
        _info(f"  3. Start Assistant:          {BOLD}assistant start{NC}")
        _info(f"  Full config reference:  {BOLD}{example}{NC}")
    else:
        _info(f"  1. Review your config:  cat {config_path}")
        _info("  2. Check your setup:    assistant doctor")
        _info("  3. Start Assistant:          assistant start")
        _info(f"  Full config reference:  {example}")
    print()


def run_setup(reconfigure: bool = False) -> int:
    """Run the interactive setup wizard. Returns exit code."""
    config_path = PROJECT_ROOT / "config.yaml"
    secrets_path = PROJECT_ROOT / "secrets.yaml"

    print()
    title = f"  {BOLD}Assistant Setup Wizard{NC}" if _color() else "  Assistant Setup Wizard"
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
