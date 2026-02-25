from __future__ import annotations

import logging
from dataclasses import dataclass

import frontmatter

from app import queue
from app.config import config
from app.evaluate import (
    MISSING,
    evaluate_automations,
    resolve_action_provenance,
    unwrap_actions,
)
from .const import DEFAULT_CLASSIFICATIONS, DETERMINISTIC_SOURCES
from .store import IssueStore

log = logging.getLogger(__name__)


@dataclass
class IssueSnapshot:
    """Lightweight reconstruction of issue state from note frontmatter.

    Used by github.issues.evaluate to run automation rules without a GitHub API call.
    """

    org: str
    repo: str
    number: int
    author: str
    title: str
    state: str
    labels: list[str]
    comment_count: int


def _snapshot_from_frontmatter(meta: dict) -> IssueSnapshot:
    return IssueSnapshot(
        org=meta.get("org", ""),
        repo=meta.get("repo", ""),
        number=int(meta.get("number", 0)),
        author=meta.get("author", ""),
        title=meta.get("title", ""),
        state=meta.get("state", "open"),
        labels=meta.get("labels", []),
        comment_count=int(meta.get("comment_count", 0)),
    )


def _make_resolver(snapshot: IssueSnapshot):
    """Return a resolve_value callable for the shared evaluation engine."""
    def resolve_value(key: str, classification: dict):
        if key.startswith("classification."):
            cls_key = key[len("classification."):]
            return classification.get(cls_key, MISSING)
        return getattr(snapshot, key, MISSING)

    return resolve_value


def handle(task: dict):
    integration_id = task["payload"]["integration"]
    integration = config.get_integration(integration_id)
    platform = config.get_platform(integration_id, "issues")
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.issues.evaluate: %s/%s#%d (integration=%s)", org, repo, number, integration_id)

    store = IssueStore(
        path=config.directories.notes / "github" / "issues" / integration.name
    )

    note_path = store.find(org, repo, number)
    if note_path is None:
        log.error("github.issues.evaluate: no note found for %s/%s#%d", org, repo, number)
        return

    post = frontmatter.load(note_path)
    meta = post.metadata

    snapshot = _snapshot_from_frontmatter(meta)
    classification = meta.get("classification", {})

    classifications = platform.classifications or DEFAULT_CLASSIFICATIONS
    resolve_value = _make_resolver(snapshot)
    actions = evaluate_automations(platform.automations, resolve_value, classification, classifications)

    if not actions:
        log.info("github.issues.evaluate: no automations matched for %s/%s#%d", org, repo, number)
        return

    provenance = resolve_action_provenance(
        platform.automations, resolve_value, classification,
        classifications, DETERMINISTIC_SOURCES,
    )

    queue.enqueue({
        "type": "github.issues.act",
        "integration": integration_id,
        "org": org,
        "repo": repo,
        "number": number,
        "actions": unwrap_actions(actions),
    }, priority=7, provenance=provenance)
    log.info(
        "github.issues.evaluate: queued act for %s/%s#%d actions=%s provenance=%s",
        org, repo, number, unwrap_actions(actions), provenance,
    )
