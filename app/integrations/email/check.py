import logging

from app import queue
from app.config import config
from .mail import Mailbox
from .store import EmailStore

log = logging.getLogger(__name__)


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name, "email")
    log.info("email.check: starting (integration=%s)", integration_name)

    notes_dir = config.directories.notes
    store = EmailStore(path=notes_dir / "emails" / integration.name)

    with Mailbox(
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        limit = task["payload"].get("limit", integration.limit)
        inbox_pairs = mb.inbox_message_ids(limit=limit)

    # Build message_id -> uid mapping. For emails without a Message-ID, use
    # the synthetic imap_{uid} key so they are still trackable.
    inbox_by_mid: dict[str, str] = {}
    for uid, mid in inbox_pairs:
        key = mid if mid else f"imap_{uid}"
        inbox_by_mid[key] = uid

    inbox_mids = set(inbox_by_mid.keys())
    note_mids = store.inbox_message_ids()

    # Notes whose emails are no longer in the inbox — move to synced/.
    synced = note_mids - inbox_mids
    for mid in synced:
        log.human("email %s no longer in inbox — moved to synced/", mid)
        store.move_to_subdir(mid, "synced")

    # Enqueue collect for every inbox email (upsert: creates new or refreshes mutable fields).
    for mid, uid in inbox_by_mid.items():
        queue.enqueue({
            "type": "email.collect",
            "integration": integration_name,
            "uid": uid,
        }, priority=3)

    log.info(
        "email.check: %d in inbox, %d in notes, %d moved to synced/, %d collect tasks queued",
        len(inbox_mids), len(note_mids), len(synced), len(inbox_by_mid),
    )
