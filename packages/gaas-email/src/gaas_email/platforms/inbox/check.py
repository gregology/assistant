import logging
import re
from datetime import date, timedelta

from gaas_sdk import runtime
from gaas_sdk.task import TaskRecord
from .store import EmailStore

log = logging.getLogger(__name__)


def _parse_window_days(window: str) -> int:
    """Parse a window string like '7d' or '30d' into a number of days.

    Only day-granularity is supported because IMAP SINCE is day-granularity.
    """
    match = re.fullmatch(r"(\d+)\s*d", window.strip().lower())
    if not match:
        raise ValueError(f"Invalid window format: {window!r} (expected e.g. '7d', '30d')")
    return int(match.group(1))


def handle(task: TaskRecord):
    from ...mail import Mailbox

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    platform = runtime.get_platform(integration_id, "inbox")
    log.info("email.inbox.check: starting (integration=%s)", integration_id)

    notes_dir = runtime.get_notes_dir()
    store = EmailStore(path=notes_dir / "emails" / integration.name)

    with Mailbox(
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        limit = task["payload"].get("limit", platform.limit)
        window = task["payload"].get("window", platform.window)
        since = date.today() - timedelta(days=_parse_window_days(window)) if window else None
        inbox_pairs = mb.inbox_message_ids(limit=limit, since=since)

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
        log.info("email %s no longer in inbox — moved to synced/", mid)
        store.move_to_subdir(mid, "synced")

    # Enqueue collect for every inbox email (upsert: creates new or refreshes mutable fields).
    for mid, uid in inbox_by_mid.items():
        runtime.enqueue({
            "type": "email.inbox.collect",
            "integration": integration_id,
            "uid": uid,
        }, priority=3)

    log.info(
        "email.inbox.check: %d in inbox, %d in notes, %d moved to synced/, %d collect tasks queued",
        len(inbox_mids), len(note_mids), len(synced), len(inbox_by_mid),
    )
