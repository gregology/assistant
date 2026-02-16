from __future__ import annotations

import logging
import secrets
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.github import GitHubClient
from app.github_store import PullRequestStore
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
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("classify_github_pr: %s/%s#%d", org, repo, number)

    client = GitHubClient()
    detail = client.get_pr_detail(org, repo, number)
    diff = client.get_pr_diff(org, repo, number)

    prompt = _render_prompt(detail, diff)
    log.info("classify_github_pr_prompt:\n%s", prompt)

    conversation = LLMConversation(
        model="fast",
        system="Disable internal monologue. Answer directly. Respond with JSON.",
    )
    classification = conversation.message(prompt=prompt, schema=CLASSIFY_SCHEMA)

    log.info("classify_github_pr: %s/%s#%d result=%s", org, repo, number, classification)

    store = PullRequestStore()
    store.update(org, repo, number, classification=classification)
