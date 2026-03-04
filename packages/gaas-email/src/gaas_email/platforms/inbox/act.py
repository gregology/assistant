import logging

from gaas_sdk import runtime
from gaas_sdk.task import TaskRecord
from .const import SIMPLE_ACTIONS
from .store import EmailStore

log = logging.getLogger(__name__)

# Actions that move the email to a different IMAP folder — the note mirrors it.
_FOLDER_MOVES: frozenset[str] = frozenset({"archive", "spam", "trash"})


def _execute_action(email, action) -> None:
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


def handle(task: TaskRecord):
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
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        email = mb.get_email(uid)
        message_id = email._message_id or f"imap_{uid}"

        for action in actions:
            _execute_action(email, action)
            if store:
                if isinstance(action, str) and action in _FOLDER_MOVES:
                    store.move_to_subdir(message_id, "synced")
                elif isinstance(action, dict) and "move_to" in action:
                    store.move_to_subdir(message_id, "synced")
