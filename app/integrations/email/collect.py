import logging

from app import queue
from app.config import config
from .mail import Mailbox
from .store import EmailStore

log = logging.getLogger(__name__)


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name, "email")
    uid = task["payload"]["uid"]
    log.info("email.collect: uid=%s (integration=%s)", uid, integration_name)

    with Mailbox(
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        email = mb.get_email(uid)

    notes_dir = config.directories.notes
    store = EmailStore(path=notes_dir / "emails" / integration.name)
    message_id = email._message_id or f"imap_{uid}"

    if store.find_by_message_id(message_id):
        store.update_mutable(message_id, email)
        log.info("email.collect: updated mutable fields for uid=%s", uid)
    else:
        store.save(email)
        log.info("email.collect: saved new email uid=%s", uid)

    priority = 6 if all(email.authentication.values()) else 9
    queue.enqueue({
        "type": "email.classify",
        "integration": integration_name,
        "uid": uid,
    }, priority=priority)
    log.info("email.collect: queued email.classify for uid=%s", uid)
