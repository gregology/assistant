"""github.evaluate handler.

Reads PR note from filesystem, evaluates automation rules deterministically.
No GitHub API calls — runs entirely from note frontmatter.

Phase 4 note: does NOT enqueue github.act. When Phase 4 is implemented,
add queue.enqueue("github.act", ...) after provenance resolution.
"""

import logging
from dataclasses import dataclass

import frontmatter

import app.human_log  # noqa: F401 — registers log.human()
from app.config import (
    AutomationConfig,
    ClassificationConfig,
    YoloAction,
    config,
    resolve_provenance,
)
from app.integrations.conditions import (
    check_condition as _check_condition,
    check_deterministic_condition as _check_deterministic_condition,
)
from app.integrations.github.const import DEFAULT_CLASSIFICATIONS, DETERMINISTIC_SOURCES
from .store import PullRequestStore

log = logging.getLogger(__name__)

_MISSING = object()


@dataclass
class PRSnapshot:
    """Lightweight reconstruction of PR state from note frontmatter.

    Used by github.evaluate to run automation rules without a GitHub API call.
    All fields reflect the state at the time the note was last written by
    github.classify_pr or github.update_prs.
    """

    org: str
    repo: str
    number: int
    author: str
    title: str
    status: str
    draft: bool
    additions: int
    deletions: int
    changed_files: int


def _snapshot_from_frontmatter(meta: dict) -> PRSnapshot:
    return PRSnapshot(
        org=meta.get("org", ""),
        repo=meta.get("repo", ""),
        number=int(meta.get("number", 0)),
        author=meta.get("author", ""),
        title=meta.get("title", ""),
        status=meta.get("status", "open"),
        draft=meta.get("draft", False),
        additions=int(meta.get("additions", 0)),
        deletions=int(meta.get("deletions", 0)),
        changed_files=int(meta.get("changed_files", 0)),
    )


def _resolve_value(key: str, snapshot: PRSnapshot, classification: dict):
    """Resolve a condition key to a value from the PR snapshot.

    Supports:
      classification.<key>  — from LLM classification output
      <field>               — direct attribute on PRSnapshot

    No authentication.* or calendar.* namespaces (GitHub has neither).
    Returns _MISSING if the key cannot be resolved.
    """
    if key.startswith("classification."):
        cls_key = key[len("classification."):]
        return classification.get(cls_key, _MISSING)
    return getattr(snapshot, key, _MISSING)


def _conditions_match(
    when: dict,
    snapshot: PRSnapshot,
    result: dict,
    classifications: dict[str, ClassificationConfig],
) -> bool:
    for key, condition in when.items():
        value = _resolve_value(key, snapshot, result)
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
    snapshot: PRSnapshot,
    result: dict,
    classifications: dict[str, ClassificationConfig],
) -> list:
    actions = []
    for automation in automations:
        if _conditions_match(automation.when, snapshot, result, classifications):
            actions.extend(automation.then)
    return actions


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name)
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.evaluate: %s/%s#%d (integration=%s)", org, repo, number, integration_name)

    pr_path = config.directories.notes / "github" / "pull_requests"
    store = PullRequestStore(path=pr_path)
    filepath = store.find(org, repo, number)
    if filepath is None:
        log.error("github.evaluate: no note found for %s/%s#%d", org, repo, number)
        return

    post = frontmatter.load(filepath)
    meta = post.metadata

    snapshot = _snapshot_from_frontmatter(meta)
    classification = meta.get("classification", {})
    classifications = integration.classifications or DEFAULT_CLASSIFICATIONS

    actions = _evaluate_automations(integration.automations, snapshot, classification, classifications)

    if actions:
        provenances = set()
        for automation in integration.automations:
            if _conditions_match(automation.when, snapshot, classification, classifications):
                provenances.add(resolve_provenance(automation.when, DETERMINISTIC_SOURCES))
        if "llm" in provenances or "hybrid" in provenances:
            provenance = "hybrid" if "rule" in provenances else "llm"
        else:
            provenance = "rule"

        unwrapped = [a.value if isinstance(a, YoloAction) else a for a in actions]

        log.human(
            "PR %s/%s#%d matched automations — actions=%s provenance=%s (act deferred to Phase 4)",
            org, repo, number, unwrapped, provenance,
        )
        log.info(
            "github.evaluate: %s/%s#%d would act: actions=%s provenance=%s",
            org, repo, number, unwrapped, provenance,
        )
    else:
        log.info("github.evaluate: %s/%s#%d no automations matched", org, repo, number)
