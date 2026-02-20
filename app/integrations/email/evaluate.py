import logging
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
from app.integrations.conditions import (
    check_condition as _check_condition,
    check_deterministic_condition as _check_deterministic_condition,
    eval_operator as _eval_operator,
    eval_now_operator as _eval_now_operator,
)
from app.integrations.email.const import DEFAULT_CLASSIFICATIONS, DETERMINISTIC_SOURCES
from .store import EmailStore

log = logging.getLogger(__name__)

_MISSING = object()


@dataclass
class EmailSnapshot:
    """Lightweight reconstruction of email state from note frontmatter.

    Used by email.evaluate to run automation rules without an IMAP connection.
    All fields reflect the state at the time the note was last written by
    email.collect. IMAP flag fields (is_read, is_starred, is_answered) are
    updated on every collect cycle and are therefore current within one cycle.
    """

    from_address: str
    domain: str
    is_noreply: bool
    is_calendar_event: bool
    is_reply: bool
    is_forward: bool
    is_unsubscribable: bool
    has_attachments: bool
    is_read: bool
    is_starred: bool
    is_answered: bool
    authentication: dict
    calendar: dict | None


def _snapshot_from_frontmatter(meta: dict) -> EmailSnapshot:
    return EmailSnapshot(
        from_address=meta.get("from_address", ""),
        domain=meta.get("domain", ""),
        is_noreply=meta.get("is_noreply", False),
        is_calendar_event=meta.get("is_calendar_event", False),
        is_reply=meta.get("is_reply", False),
        is_forward=meta.get("is_forward", False),
        is_unsubscribable=meta.get("is_unsubscribable", False),
        has_attachments=meta.get("has_attachments", False),
        is_read=meta.get("is_read", False),
        is_starred=meta.get("is_starred", False),
        is_answered=meta.get("is_answered", False),
        authentication=meta.get("authentication", {}),
        calendar=meta.get("calendar"),
    )


def _resolve_value(key: str, snapshot: EmailSnapshot, classification: dict):
    """Resolve a namespaced condition key to a value from the email snapshot.

    Returns _MISSING if the key cannot be resolved.
    """
    if key.startswith("classification."):
        cls_key = key[len("classification."):]
        return classification.get(cls_key, _MISSING)

    if key.startswith("authentication."):
        auth_key = key[len("authentication."):]
        return snapshot.authentication.get(auth_key, _MISSING)

    if key.startswith("calendar."):
        if snapshot.calendar is None:
            return _MISSING
        cal_key = key[len("calendar."):]
        return snapshot.calendar.get(cal_key, _MISSING)

    return getattr(snapshot, key, _MISSING)


def _conditions_match(
    when: dict,
    snapshot: EmailSnapshot,
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
    snapshot: EmailSnapshot,
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
    message_id = task["payload"]["message_id"]
    log.info("email.evaluate: message_id=%s (integration=%s)", message_id, integration_name)

    notes_dir = config.directories.notes
    store = EmailStore(path=notes_dir / "emails" / integration.name)

    filepath = store.find_by_message_id(message_id)
    if filepath is None:
        log.error("email.evaluate: no note found for message_id=%s", message_id)
        return

    post = frontmatter.load(filepath)
    meta = post.metadata

    snapshot = _snapshot_from_frontmatter(meta)
    classification = meta.get("classification", {})
    uid = str(meta.get("uid", ""))

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

        queue.enqueue({
            "type": "email.act",
            "integration": integration_name,
            "uid": uid,
            "actions": unwrapped,
        }, priority=7, provenance=provenance)
        log.info(
            "email.evaluate: queued email.act for message_id=%s actions=%s provenance=%s",
            message_id, unwrapped, provenance,
        )
