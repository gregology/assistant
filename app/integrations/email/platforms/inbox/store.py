from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter

from app.store import NoteStore

if TYPE_CHECKING:
    from ...mail import Email

log = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r"[^a-zA-Z0-9._-]")


def _sanitize_message_id(message_id: str) -> str:
    """Convert a raw Message-ID to a safe filename component.

    Strips surrounding angle brackets and replaces any character that is not
    alphanumeric, a dot, a hyphen, or an underscore with an underscore.

    >>> _sanitize_message_id("<CABcd.123+tag@mail.gmail.com>")
    'CABcd.123_tag_mail.gmail.com'
    """
    mid = message_id.strip().strip("<>")
    return _UNSAFE_CHARS.sub("_", mid)


class EmailStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._store = NoteStore(self._path)

    def inbox_message_ids(self) -> set[str]:
        """Return message IDs of notes in the inbox root directory only.

        Notes in synced/ or other subdirectories are excluded — they are no
        longer considered inbox emails.
        """
        if not self._path.is_dir():
            return set()
        ids: set[str] = set()
        for f in self._path.glob("*.md"):
            try:
                post = frontmatter.load(f)
                mid = post.metadata.get("message_id")
                if mid:
                    ids.add(str(mid))
                else:
                    uid = post.metadata.get("uid")
                    if uid:
                        ids.add(f"imap_{uid}")
            except Exception:
                log.warning("Failed to parse front matter: %s", f)
        return ids

    def known_message_ids(self) -> set[str]:
        """Return all known raw Message-IDs across the entire note tree.

        For notes saved without a Message-ID (malformed emails), returns the
        synthetic ``imap_{uid}`` key so they are still recognised during the
        reconciliation loop in email.inbox.check.
        """
        if not self._path.is_dir():
            return set()
        ids: set[str] = set()
        for f in self._path.rglob("*.md"):
            try:
                post = frontmatter.load(f)
                mid = post.metadata.get("message_id")
                if mid:
                    ids.add(str(mid))
                else:
                    uid = post.metadata.get("uid")
                    if uid:
                        ids.add(f"imap_{uid}")
            except Exception:
                log.warning("Failed to parse front matter: %s", f)
        return ids

    def find_by_message_id(self, message_id: str) -> Path | None:
        """Find a note file by its raw Message-ID or synthetic ``imap_{uid}`` key.

        Sanitizes the message_id internally before constructing the glob pattern.
        """
        if not self._path.is_dir() or not message_id:
            return None
        sanitized = _sanitize_message_id(message_id)
        if not sanitized:
            return None
        matches = list(self._path.rglob(f"*__{sanitized}.md"))
        return matches[0] if matches else None

    def move_to_subdir(self, message_id: str, subdir: str) -> None:
        """Move a note to a subdirectory mirroring the email's IMAP folder."""
        filepath = self.find_by_message_id(message_id)
        if filepath is None:
            log.warning("Cannot move note for message_id=%s: not found", message_id)
            return
        dest_dir = self._path / subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        filepath.rename(dest_dir / filepath.name)
        log.info("Moved note message_id=%s to %s/", message_id, subdir)

    def update_mutable(self, message_id: str, email: Email) -> Path | None:
        """Update only the mutable IMAP-flag fields on an existing note."""
        return self.update(
            message_id,
            uid=email._uid,
            is_read=email.is_read,
            is_starred=email.is_starred,
            is_answered=email.is_answered,
        )

    def save(self, email: Email) -> Path:
        raw_mid = email._message_id
        suffix = _sanitize_message_id(raw_mid) if raw_mid else f"imap_{email._uid}"
        filename = email.date.strftime("%Y_%m_%d_%H_%M_%S") + f"__{suffix}.md"
        fields = dict(
            uid=email._uid,
            message_id=raw_mid,
            from_address=email.from_address,
            to_address=email.to_address,
            subject=email.subject,
            received_at=email.date.isoformat(),
            authentication=email.authentication,
            domain=email.domain,
            is_noreply=email.is_noreply,
            is_calendar_event=email.is_calendar_event,
            is_reply=email.is_reply,
            is_forward=email.is_forward,
            is_unsubscribable=email.is_unsubscribable,
            has_attachments=email.has_attachments,
            is_read=email.is_read,
            is_starred=email.is_starred,
            is_answered=email.is_answered,
        )
        if email.calendar is not None:
            fields["calendar"] = email.calendar
        return self._store.save(filename, **fields)

    def update(self, message_id: str, **fields) -> Path | None:
        filepath = self.find_by_message_id(message_id)
        if filepath is None:
            log.error("No file found for message_id=%s", message_id)
            return None
        post = frontmatter.load(filepath)
        for key, value in fields.items():
            post[key] = value
        filepath.write_text(frontmatter.dumps(post))
        log.info("Updated %s", filepath)
        return filepath
