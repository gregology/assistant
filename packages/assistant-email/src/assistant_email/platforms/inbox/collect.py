import logging

from assistant_sdk import runtime
from assistant_sdk.task import TaskRecord
from .store import EmailStore

log = logging.getLogger(__name__)


def handle(task: TaskRecord) -> None:
    from ...mail import Mailbox

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    uid = task["payload"]["uid"]
    log.info("email.inbox.collect: uid=%s (integration=%s)", uid, integration_id)

    with Mailbox(
        imap_server=integration.imap_server,  # type: ignore[attr-defined]
        imap_port=integration.imap_port,  # type: ignore[attr-defined]
        username=integration.username,  # type: ignore[attr-defined]
        password=integration.password,  # type: ignore[attr-defined]
    ) as mb:
        email = mb.get_email(uid)

    notes_dir = runtime.get_notes_dir()
    store = EmailStore(path=notes_dir / "emails" / integration.name)
    message_id = email._message_id or f"imap_{uid}"

    if store.find_by_message_id(message_id):
        store.update_mutable(message_id, email)
        log.info("email.inbox.collect: updated mutable fields for uid=%s", uid)
    else:
        store.save(email)
        log.info("email.inbox.collect: saved new email uid=%s", uid)

    priority = 6 if all(email.authentication.values()) else 9
    runtime.enqueue(
        {
            "type": "email.inbox.classify",
            "integration": integration_id,
            "uid": uid,
        },
        priority=priority,
    )
    log.info("email.inbox.collect: queued classify for uid=%s", uid)
