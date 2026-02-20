from __future__ import annotations

import logging
import secrets
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import config
from .client import GitHubClient
from .store import PullRequestStore
from app.llm import LLMConversation

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

MAX_DIFF_CHARS = 10_000

CLASSIFY_SCHEMA = {
    "properties": {
        "complexity": {"type": "number"},
        "risk": {"type": "number"},
        "documentation_only": {"type": "number"},
    },
    "required": ["complexity", "risk", "documentation_only"],
}


def _render_prompt(detail: dict, diff: str) -> str:
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... (diff truncated)"
    template = jinja_env.get_template("classify_github_pr.jinja")
    return template.render(
        beginning_salt=secrets.token_hex(16),
        end_salt=secrets.token_hex(16),
        title=detail["title"],
        author=detail["author"],
        body=detail["body"],
        additions=detail["additions"],
        deletions=detail["deletions"],
        changed_files=detail["changed_files"],
        diff=diff,
    )


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name)
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.classify_pr: %s/%s#%d (integration=%s)", org, repo, number, integration_name)

    client = GitHubClient()
    detail = client.get_pr_detail(org, repo, number)
    diff = client.get_pr_diff(org, repo, number)

    prompt = _render_prompt(detail, diff)
    log.info("github.classify_pr prompt:\n%s", prompt)

    conversation = LLMConversation(
        model=integration.llm,
        system="Disable internal monologue. Answer directly. Respond with JSON.",
    )
    classification = conversation.message(prompt=prompt, schema=CLASSIFY_SCHEMA)

    log.info("github.classify_pr: %s/%s#%d result=%s", org, repo, number, classification)

    pr_path = config.directories.notes / "github" / "pull_requests"
    store = PullRequestStore(path=pr_path)
    store.update(org, repo, number, classification=classification)
