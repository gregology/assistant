from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

import frontmatter
from jinja2 import Environment, FileSystemLoader

from app import queue
from app.config import ClassificationConfig, config
from app.integrations.github.const import DEFAULT_CLASSIFICATIONS
from app.llm import LLMConversation
from .client import GitHubClient
from .store import PullRequestStore

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
jinja_env.filters["scrub"] = lambda s: str(s).replace("END UNTRUSTED", "")

MAX_DIFF_CHARS = 10_000

_TYPE_TO_SCHEMA = {
    "confidence": lambda _cls: {"type": "number"},
    "boolean": lambda _cls: {"type": "boolean"},
    "enum": lambda cls: {"type": "string", "enum": cls.values},
}


def _build_schema(classifications: dict[str, ClassificationConfig]) -> dict:
    properties = {}
    for name, cls in classifications.items():
        properties[name] = _TYPE_TO_SCHEMA[cls.type](cls)
    return {
        "properties": properties,
        "required": list(classifications.keys()),
    }


def _render_prompt(
    detail: dict,
    diff: str,
    classifications: dict[str, ClassificationConfig],
) -> str:
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... (diff truncated)"
    template = jinja_env.get_template("classify_github_pr.jinja")
    return template.render(
        salt=secrets.token_hex(4).upper(),
        title=detail["title"],
        author=detail["author"],
        body=detail["body"],
        additions=detail["additions"],
        deletions=detail["deletions"],
        changed_files=detail["changed_files"],
        diff=diff,
        classifications=classifications,
    )


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name, "github")
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.classify_pr: %s/%s#%d (integration=%s)", org, repo, number, integration_name)

    classifications = integration.classifications or DEFAULT_CLASSIFICATIONS
    llm_config = config.llms[integration.llm]

    store = PullRequestStore(
        path=config.directories.notes / "github" / "pull_requests" / integration.name
    )

    # Check if all classifications are already present — skip LLM if so.
    note_path = store.find(org, repo, number)
    existing_cls = {}
    if note_path:
        post = frontmatter.load(note_path)
        existing_cls = post.metadata.get("classification", {})

    if all(k in existing_cls for k in classifications):
        log.info(
            "github.classify_pr: %s/%s#%d all classifications present, skipping LLM",
            org, repo, number,
        )
    else:
        client = GitHubClient()
        detail = client.get_pr_detail(org, repo, number)
        diff = client.get_pr_diff(org, repo, number)

        prompt = _render_prompt(detail, diff, classifications)
        log.info("github.classify_pr prompt:\n%s", prompt)

        conversation = LLMConversation(
            model=integration.llm,
            system="Disable internal monologue. Answer directly. Respond with JSON.",
        )
        schema = _build_schema(classifications)
        classification = conversation.message(prompt=prompt, schema=schema)

        log.info("github.classify_pr: %s/%s#%d result=%s", org, repo, number, classification)

        classified_by = {
            "model": llm_config.model,
            "profile": integration.llm,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        store.update(org, repo, number, classification=classification, classified_by=classified_by)
        log.human("Classified PR **%s/%s#%d**", org, repo, number)

    queue.enqueue({
        "type": "github.evaluate",
        "integration": integration_name,
        "org": org,
        "repo": repo,
        "number": number,
    }, priority=7)
    log.info("github.classify_pr: queued github.evaluate for %s/%s#%d", org, repo, number)
