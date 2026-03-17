from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import frontmatter

from assistant_sdk.actions import enqueue_actions
from assistant_sdk import runtime
from assistant_sdk.evaluate import (
    MISSING,
    evaluate_automations,
    resolve_action_provenance,
    unwrap_actions,
)
from assistant_sdk.protocols import ResolveValue
from assistant_sdk.task import TaskRecord
from .const import DEFAULT_CLASSIFICATIONS, DETERMINISTIC_SOURCES
from .store import PullRequestStore

log = logging.getLogger(__name__)


@dataclass
class PRSnapshot:
    """Lightweight reconstruction of PR state from note frontmatter.

    Used by github.pull_requests.evaluate to run automation rules without a GitHub API call.
    All fields reflect the state at the time the note was last written by
    github.pull_requests.collect.
    """

    org: str
    repo: str
    number: int
    author: str
    title: str
    status: str
    additions: int
    deletions: int
    changed_files: int


def _snapshot_from_frontmatter(meta: dict[str, Any]) -> PRSnapshot:
    return PRSnapshot(
        org=meta.get("org", ""),
        repo=meta.get("repo", ""),
        number=int(meta.get("number", 0)),
        author=meta.get("author", ""),
        title=meta.get("title", ""),
        status=meta.get("status", "open"),
        additions=int(meta.get("additions", 0)),
        deletions=int(meta.get("deletions", 0)),
        changed_files=int(meta.get("changed_files", 0)),
    )


def _make_resolver(snapshot: PRSnapshot) -> ResolveValue:
    """Return a resolve_value callable for the shared evaluation engine."""
    def resolve_value(key: str, classification: dict[str, Any]) -> Any:
        if key.startswith("classification."):
            cls_key = key[len("classification."):]
            return classification.get(cls_key, MISSING)
        return getattr(snapshot, key, MISSING)

    return resolve_value


def handle(task: TaskRecord) -> None:
    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    platform = runtime.get_platform(integration_id, "pull_requests")
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info(
        "github.pull_requests.evaluate: %s/%s#%d (integration=%s)",
        org, repo, number, integration_id,
    )

    store = PullRequestStore(
        path=runtime.get_notes_dir() / "github" / "pull_requests" / integration.name
    )

    note_path = store.find(org, repo, number)
    if note_path is None:
        log.error("github.pull_requests.evaluate: no note found for %s/%s#%d", org, repo, number)
        return

    post = frontmatter.load(note_path)
    meta = post.metadata

    snapshot = _snapshot_from_frontmatter(meta)
    classification = meta.get("classification", {})

    classifications = platform.classifications or DEFAULT_CLASSIFICATIONS
    resolve_value = _make_resolver(snapshot)
    actions = evaluate_automations(
        platform.automations, resolve_value, classification, classifications,
    )

    if not actions:
        log.info(
            "github.pull_requests.evaluate: no automations matched for %s/%s#%d",
            org, repo, number,
        )
        return

    provenance = resolve_action_provenance(
        platform.automations, resolve_value, classification,
        classifications, DETERMINISTIC_SOURCES,
    )

    log.info(
        "github.pull_requests.evaluate: queuing actions for %s/%s#%d actions=%s provenance=%s",
        org, repo, number, unwrap_actions(actions), provenance,
    )
    enqueue_actions(
        actions=actions,
        platform_payload={
            "type": "github.pull_requests.act",
            "integration": integration_id,
            "org": org,
            "repo": repo,
            "number": number,
        },
        resolve_value=resolve_value,
        classification=classification,
        provenance=provenance,
        priority=7,
    )
