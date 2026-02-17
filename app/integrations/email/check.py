import logging

from app import queue
from app.config import config
from app.mail import Mailbox
from app.store import EmailStore

log = logging.getLogger(__name__)


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name)
    log.info("email.check: starting (integration=%s)", integration_name)

    notes_dir = config.directories.notes
    store = EmailStore(path=notes_dir / "emails" / integration.username)
    known = store.known_uids()
    log.info("email.check: found %d existing email files", len(known))

    with Mailbox(
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        limit = task["payload"].get("limit", integration.limit)
        mb.collect_emails(limit=limit)

        existing = []
        new = []
        for email in mb.emails:
            if email._uid in known:
                existing.append(email._uid)
            else:
                new.append(email._uid)

    log.info("email.check: %d existing emails: %s", len(existing), existing)
    log.info("email.check: %d new emails: %s", len(new), new)

    for uid in new:
        queue.enqueue({
            "type": "email.collect",
            "integration": integration_name,
            "uid": uid,
        }, priority=3)
