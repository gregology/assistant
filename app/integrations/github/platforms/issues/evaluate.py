"""Stub for github.issues.evaluate — automation evaluation not yet implemented.

This handler logs evaluation results without triggering actions.
Full automation dispatch will be implemented when issue actions are added.
"""
from __future__ import annotations

import logging
import operator
import re
from dataclasses import dataclass

import frontmatter

from app import queue
from app.config import (
    AutomationConfig,
    ClassificationConfig,
    YoloAction,
    config,
    resolve_provenance,
)
from .const import DEFAULT_CLASSIFICATIONS, DETERMINISTIC_SOURCES
from .store import IssueStore

log = logging.getLogger(__name__)

_OPS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
}

_OP_RE = re.compile(r"^\s*(>=|<=|>|<|==)\s*(\d+\.?\d*)\s*$")


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


_MISSING = object()


def _eval_operator(value: float, expr: str) -> bool:
    match = _OP_RE.match(expr)
    if not match:
        log.warning("Invalid confidence condition: %r", expr)
        return False
    op_fn = _OPS[match.group(1)]
    threshold = float(match.group(2))
    return op_fn(value, threshold)


def _check_condition(value, condition, cls_config: ClassificationConfig) -> bool:
    if cls_config.type == "boolean":
        return value is condition

    if cls_config.type == "confidence":
        if isinstance(condition, (int, float)):
            return value >= condition
        if isinstance(condition, str):
            return _eval_operator(value, condition)
        return False

    if cls_config.type == "enum":
        if isinstance(condition, list):
            return value in condition
        return value == condition

    return False


def _check_deterministic_condition(value, condition) -> bool:
    if isinstance(condition, bool):
        return value is condition
    if isinstance(condition, list):
        return value in condition
    return value == condition


def _resolve_value(key: str, snapshot: IssueSnapshot, classification: dict):
    """Resolve a namespaced condition key to a value from the issue snapshot."""
    if key.startswith("classification."):
        cls_key = key[len("classification."):]
        return classification.get(cls_key, _MISSING)
    return getattr(snapshot, key, _MISSING)


def _conditions_match(
    when: dict,
    snapshot: IssueSnapshot,
    classification: dict,
    classifications: dict[str, ClassificationConfig],
) -> bool:
    for key, condition in when.items():
        value = _resolve_value(key, snapshot, classification)
        if value is _MISSING:
            return False
        if key.startswith("classification."):
            cls_key = key[len("classification."):]
            if cls_key not in classifications:
                return False
            if not _check_condition(value, condition, classifications[cls_key]):
                return False
        else:
            if not _check_deterministic_condition(value, condition):
                return False
    return True


def _evaluate_automations(
    automations: list[AutomationConfig],
    snapshot: IssueSnapshot,
    classification: dict,
    classifications: dict[str, ClassificationConfig],
) -> list:
    actions = []
    for automation in automations:
        if _conditions_match(automation.when, snapshot, classification, classifications):
            actions.extend(automation.then)
    return actions


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name, "github")
    platform = config.get_platform(integration_name, "github", "issues")
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.issues.evaluate: %s/%s#%d (integration=%s)", org, repo, number, integration_name)

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
    actions = _evaluate_automations(platform.automations, snapshot, classification, classifications)

    if not actions:
        log.info("github.issues.evaluate: no automations matched for %s/%s#%d", org, repo, number)
        return

    provenances = set()
    for automation in platform.automations:
        if _conditions_match(automation.when, snapshot, classification, classifications):
            provenances.add(resolve_provenance(automation.when, DETERMINISTIC_SOURCES))
    if "llm" in provenances or "hybrid" in provenances:
        provenance = "hybrid" if "rule" in provenances else "llm"
    else:
        provenance = "rule"

    unwrapped = [a.value if isinstance(a, YoloAction) else a for a in actions]

    queue.enqueue({
        "type": "github.issues.act",
        "integration": integration_name,
        "org": org,
        "repo": repo,
        "number": number,
        "actions": unwrapped,
    }, priority=7, provenance=provenance)
    log.info(
        "github.issues.evaluate: queued act for %s/%s#%d actions=%s provenance=%s",
        org, repo, number, unwrapped, provenance,
    )
