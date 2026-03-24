"""GitHub integration setup hook for the setup wizard.

Prompts for GitHub App credentials and returns integration config + secrets.
Called by the core app's setup wizard when this integration is discovered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class SetupPrompts(Protocol):
    def prompt(self, msg: str, default: str = "") -> str: ...
    def prompt_yn(self, msg: str, default: bool = True) -> bool: ...
    def prompt_choice(self, msg: str, choices: list[str], default: str = "") -> str: ...
    def info(self, msg: str) -> None: ...
    def success(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def heading(self, msg: str) -> None: ...


def setup(prompts: SetupPrompts) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Configure GitHub integration. Returns (integrations_list, secrets)."""
    prompts.heading("GitHub Integration")

    if not prompts.prompt_yn("Set up GitHub integration?", default=True):
        return [], {}

    integrations: list[dict[str, Any]] = []
    secrets: dict[str, str] = {}

    name = prompts.prompt("Integration name", "my_repos")
    github_user = prompts.prompt("GitHub username")
    app_id = prompts.prompt("GitHub App ID")
    installation_id = prompts.prompt("GitHub App Installation ID")

    prompts.info("Provide the private key for your GitHub App.")
    prompts.info("You can paste the key directly or provide a path to the .pem file.\n")
    key_input = prompts.prompt("Private key (path to .pem file or paste key)")

    key_path = Path(key_input)
    if key_path.is_file():
        private_key = key_path.read_text()
        prompts.success(f"Read private key from {key_path}")
    else:
        private_key = key_input

    secret_app_id_key = f"{name}_github_app_id"
    secret_installation_id_key = f"{name}_github_installation_id"
    secret_private_key_key = f"{name}_github_private_key"
    secrets[secret_app_id_key] = app_id
    secrets[secret_installation_id_key] = installation_id
    secrets[secret_private_key_key] = private_key

    schedule = prompts.prompt("Check frequency", "10m")

    pr_enabled = prompts.prompt_yn("Monitor pull requests?", default=True)
    issues_enabled = prompts.prompt_yn("Monitor issues?", default=True)

    platforms: dict[str, Any] = {}
    if pr_enabled:
        platforms["pull_requests"] = {
            "classifications": {
                "complexity": (
                    "how complex is this PR to review? 0 = trivial, 1 = major architectural change"
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
        return [], {}

    integration: dict[str, Any] = {
        "type": "github",
        "name": name,
        "github_user": github_user,
        "app_id": f"!secret {secret_app_id_key}",
        "installation_id": f"!secret {secret_installation_id_key}",
        "private_key": f"!secret {secret_private_key_key}",
        "schedule": {"every": schedule},
        "llm": "default",
        "platforms": platforms,
    }
    integrations.append(integration)

    return integrations, secrets
