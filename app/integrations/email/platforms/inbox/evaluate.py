import logging
from dataclasses import dataclass

import frontmatter

from app import queue
from app.config import config
from app.evaluate import (
    MISSING,
    evaluate_automations,
    eval_now_operator,
    resolve_action_provenance,
    unwrap_actions,
)
from .const import DEFAULT_CLASSIFICATIONS, DETERMINISTIC_SOURCES
from .store import EmailStore

log = logging.getLogger(__name__)


@dataclass
class EmailSnapshot:
    """Lightweight reconstruction of email state from note frontmatter.

    Used by email.inbox.evaluate to run automation rules without an IMAP connection.
    All fields reflect the state at the time the note was last written by
    email.inbox.collect. IMAP flag fields (is_read, is_starred, is_answered) are
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


def _make_resolver(snapshot: EmailSnapshot):
    """Return a resolve_value callable for the shared evaluation engine."""
    def resolve_value(key: str, classification: dict):
        if key.startswith("classification."):
            cls_key = key[len("classification."):]
            return classification.get(cls_key, MISSING)

        if key.startswith("authentication."):
            auth_key = key[len("authentication."):]
            return snapshot.authentication.get(auth_key, MISSING)

        if key.startswith("calendar."):
            if snapshot.calendar is None:
                return MISSING
            cal_key = key[len("calendar."):]
            return snapshot.calendar.get(cal_key, MISSING)

        return getattr(snapshot, key, MISSING)

    return resolve_value


def handle(task: dict):
    integration_id = task["payload"]["integration"]
    integration = config.get_integration(integration_id)
    platform = config.get_platform(integration_id, "inbox")
    message_id = task["payload"]["message_id"]
    log.info("email.inbox.evaluate: message_id=%s (integration=%s)", message_id, integration_id)

    notes_dir = config.directories.notes
    store = EmailStore(path=notes_dir / "emails" / integration.name)

    filepath = store.find_by_message_id(message_id)
    if filepath is None:
        log.error("email.inbox.evaluate: no note found for message_id=%s", message_id)
        return

    post = frontmatter.load(filepath)
    meta = post.metadata

    snapshot = _snapshot_from_frontmatter(meta)
    classification = meta.get("classification", {})
    uid = str(meta.get("uid", ""))

    classifications = platform.classifications or DEFAULT_CLASSIFICATIONS
    resolve_value = _make_resolver(snapshot)
    actions = evaluate_automations(platform.automations, resolve_value, classification, classifications)

    if actions:
        provenance = resolve_action_provenance(
            platform.automations, resolve_value, classification,
            classifications, DETERMINISTIC_SOURCES,
        )

        queue.enqueue({
            "type": "email.inbox.act",
            "integration": integration_id,
            "uid": uid,
            "actions": unwrap_actions(actions),
        }, priority=7, provenance=provenance)
        log.info(
            "email.inbox.evaluate: queued act for message_id=%s actions=%s provenance=%s",
            message_id, unwrap_actions(actions), provenance,
        )
