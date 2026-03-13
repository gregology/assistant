from __future__ import annotations

import logging
import secrets
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import frontmatter

from assistant_sdk import runtime
from assistant_sdk.classify import build_schema, make_jinja_env
from assistant_sdk.models import ClassificationConfig
from assistant_sdk.task import TaskRecord

from .const import DEFAULT_CLASSIFICATIONS
from .store import IssueStore

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = make_jinja_env(TEMPLATES_DIR)

MAX_BODY_CHARS = 10_000


def _render_prompt(
    detail: dict[str, Any],
    classifications: dict[str, ClassificationConfig],
) -> str:
    body = detail["body"]
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n... (body truncated)"
    template = jinja_env.get_template("classify.jinja")
    return template.render(
        salt=secrets.token_hex(4).upper(),
        title=detail["title"],
        author=detail["author"],
        body=body,
        labels=detail["labels"],
        comment_count=detail["comment_count"],
        classifications=classifications,
    )


def handle(task: TaskRecord) -> None:
    from ...client import GitHubClient

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    platform = runtime.get_platform(integration_id, "issues")
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.issues.classify: %s/%s#%d (integration=%s)", org, repo, number, integration_id)

    classifications = platform.classifications or DEFAULT_CLASSIFICATIONS
    llm_config = runtime.get_llm_config(integration.llm)

    store = IssueStore(
        path=runtime.get_notes_dir() / "github" / "issues" / integration.name
    )

    # Check if all classifications are already present — skip LLM if so.
    note_path = store.find(org, repo, number)
    existing_cls = {}
    if note_path:
        post = frontmatter.load(note_path)
        existing_cls = post.metadata.get("classification", {})

    if all(k in existing_cls for k in classifications):
        log.info(
            "github.issues.classify: %s/%s#%d all classifications present, skipping LLM",
            org, repo, number,
        )
    else:
        client = GitHubClient()
        detail = client.get_issue_detail(org, repo, number)

        prompt = _render_prompt(detail, classifications)
        log.info("github.issues.classify prompt:\n%s", prompt)

        conversation = runtime.create_llm_conversation(
            model=integration.llm,
            system="Disable internal monologue. Answer directly. Respond with JSON.",
        )
        schema = build_schema(classifications)
        classification = conversation.message(prompt=prompt, schema=schema)

        log.info("github.issues.classify: %s/%s#%d result=%s", org, repo, number, classification)

        classified_by = {
            "model": llm_config.model,
            "profile": integration.llm,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        store.update(org, repo, number, classification=classification, classified_by=classified_by)
        log.info("Classified issue **%s/%s#%d**", org, repo, number)

    runtime.enqueue({
        "type": "github.issues.evaluate",
        "integration": integration_id,
        "org": org,
        "repo": repo,
        "number": number,
    }, priority=7)
    log.info("github.issues.classify: queued evaluate for %s/%s#%d", org, repo, number)
