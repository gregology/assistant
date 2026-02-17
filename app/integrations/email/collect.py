import logging

from app import queue
from app.config import config
from app.mail import Mailbox
from app.store import EmailStore

log = logging.getLogger(__name__)


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name)
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
    store = EmailStore(path=notes_dir / "emails" / integration.username)
    store.save(email)

    if all(email.authentication.values()):
        queue.enqueue({
            "type": "email.classify",
            "integration": integration_name,
            "uid": uid,
        }, priority=6)
        log.info("email.collect: queued email.classify for uid=%s", uid)
    else:
        queue.enqueue({
            "type": "email.classify",
            "integration": integration_name,
            "uid": uid,
        }, priority=9)
        log.info("email.collect: queued low priority email.classify for uid=%s", uid)
