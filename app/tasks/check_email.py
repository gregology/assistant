import logging

from app import queue
from app.mail import Mailbox
from app.store import EmailStore

log = logging.getLogger(__name__)


def handle(task: dict):
    log.info("check_email: starting")

    store = EmailStore()
    known = store.known_uids()
    log.info("check_email: found %d existing email files", len(known))

    with Mailbox() as mb:
        mb.collect_emails(limit=task["payload"].get("limit", 10))

        existing = []
        new = []
        for email in mb.emails:
            if email._uid in known:
                existing.append(email._uid)
            else:
                new.append(email._uid)

    log.info("check_email: %d existing emails: %s", len(existing), existing)
    log.info("check_email: %d new emails: %s", len(new), new)

    for uid in new:
        queue.enqueue({"type": "collect_email", "uid": uid})
