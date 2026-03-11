import logging
from typing import Any

from gaas_sdk import runtime
from gaas_sdk.task import TaskRecord
from .const import IRREVERSIBLE_ACTIONS, SIMPLE_ACTIONS
from .store import EmailStore

log = logging.getLogger(__name__)

# Actions that move the email to a different IMAP folder — the note mirrors it.
_FOLDER_MOVES: frozenset[str] = frozenset({"archive", "spam", "trash"})

# Provenances that are not fully deterministic.
_UNSAFE_PROVENANCES: frozenset[str] = frozenset({"llm", "hybrid"})


def _unwrap_yolo(action: Any) -> tuple[Any, bool]:
    """Unwrap a ``{"!yolo": inner}`` payload marker, returning (inner, is_yolo)."""
    if isinstance(action, dict) and "!yolo" in action:
        return action["!yolo"], True
    return action, False


def _is_irreversible(action: Any) -> bool:
    """Check if a raw action (string or dict) is irreversible."""
    if isinstance(action, str):
        return action in IRREVERSIBLE_ACTIONS
    if isinstance(action, dict):
        return bool(set(action.keys()) & IRREVERSIBLE_ACTIONS)
    return False


def _execute_action(email: Any, action: Any) -> None:
    if isinstance(action, str):
        if action not in SIMPLE_ACTIONS:
            log.warning("email.inbox.act: unknown action %r, skipping", action)
            return
        getattr(email, action)()
    elif isinstance(action, dict):
        if "draft_reply" in action:
            email.draft_reply(action["draft_reply"])
        elif "move_to" in action:
            email.move_to(action["move_to"])
        else:
            log.warning("email.inbox.act: unknown action dict %r, skipping", action)


def handle(task: TaskRecord) -> None:
    from ...mail import Mailbox

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    uid = task["payload"]["uid"]
    actions = task["payload"]["actions"]
    provenance = task.get("provenance", "unknown")
    log.info(
        "email.inbox.act: uid=%s actions=%s provenance=%s (integration=%s)",
        uid, actions, provenance, integration_id,
    )

    notes_dir = runtime.get_notes_dir()
    store = EmailStore(path=notes_dir / "emails" / integration.name) if notes_dir else None

    with Mailbox(
        imap_server=integration.imap_server,  # type: ignore[attr-defined]
        imap_port=integration.imap_port,  # type: ignore[attr-defined]
        username=integration.username,  # type: ignore[attr-defined]
        password=integration.password,  # type: ignore[attr-defined]
    ) as mb:
        email = mb.get_email(uid)
        message_id = email._message_id or f"imap_{uid}"

        for action in actions:
            action, yolo = _unwrap_yolo(action)

            if _is_irreversible(action) and provenance in _UNSAFE_PROVENANCES and not yolo:
                log.warning(
                    "email.inbox.act: BLOCKED irreversible action %r "
                    "(provenance=%s, yolo=%s), skipping",
                    action, provenance, yolo,
                )
                continue

            _execute_action(email, action)
            is_folder_move = (
                (isinstance(action, str) and action in _FOLDER_MOVES)
                or (isinstance(action, dict) and "move_to" in action)
            )
            if store and is_folder_move:
                store.move_to_subdir(message_id, "synced")
