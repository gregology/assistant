from __future__ import annotations

import re
from datetime import date, datetime, UTC
from email.message import EmailMessage
from email.policy import EmailPolicy
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import tldextract
from bs4 import BeautifulSoup
from icalendar import Calendar
from imap_tools import AND, MailBox as IMAPToolsMailBox, MailMessage

from assistant_sdk.logging import get_logger

log = get_logger(__name__)

_NOREPLY_RE = re.compile(
    r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster)@",
    re.IGNORECASE,
)

_FORWARD_RE = re.compile(r"^\s*(fwd?:|fw:|\[fwd?:)", re.IGNORECASE)


class Email:
    def __init__(self, msg: MailMessage, mailbox: Mailbox) -> None:
        self._mailbox = mailbox
        self._uid: str = msg.uid or ""
        self._message_id: str = _clean_header(msg.headers.get("message-id", ("",))[0])
        self._references: str = _clean_header(" ".join(msg.headers.get("references", ("",))))

        self.from_address: str = msg.from_
        self.from_name: str = msg.from_values.name if msg.from_values else ""
        self.to_address: str = msg.to[0] if msg.to else ""
        self.subject: str = msg.subject
        self.date: datetime = _parse_received_date(msg.headers) or (
            msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=UTC)
        )

        self.contents: str = msg.text or msg.html or ""
        self.contents_clean: str = self._clean(msg.text, msg.html)

        spf, dkim, dmarc = _parse_auth_results(msg.headers)
        self.authentication: dict[str, bool] = {
            "dkim_pass": dkim,
            "dmarc_pass": dmarc,
            "spf_pass": spf,
        }

        self.calendar: dict[str, Any] | None = _parse_calendar(msg.attachments)
        self.has_attachments: bool = _has_non_calendar_attachments(msg.attachments)

        self._flags: frozenset[str] = frozenset(msg.flags)
        self._in_reply_to: str = _clean_header(msg.headers.get("in-reply-to", ("",))[0])

        self._unsubscribe_url: str | None = _parse_unsubscribe_url(msg.headers)
        self._unsubscribe_post: bool = bool(msg.headers.get("list-unsubscribe-post", ()))

    @property
    def domain(self) -> str:
        _, _, d = self.from_address.partition("@")
        return d.lower()

    @property
    def root_domain(self) -> str:
        ext = tldextract.extract(self.from_address)
        return f"{ext.domain}.{ext.suffix}".lower()

    @property
    def is_noreply(self) -> bool:
        return bool(_NOREPLY_RE.match(self.from_address))

    @property
    def is_calendar_event(self) -> bool:
        return self.calendar is not None

    @property
    def is_reply(self) -> bool:
        return bool(self._in_reply_to)

    @property
    def is_forward(self) -> bool:
        return bool(_FORWARD_RE.match(self.subject))

    @property
    def is_read(self) -> bool:
        return "\\Seen" in self._flags

    @property
    def is_starred(self) -> bool:
        return "\\Flagged" in self._flags

    @property
    def is_answered(self) -> bool:
        return "\\Answered" in self._flags

    @property
    def is_unsubscribable(self) -> bool:
        return self.unsubscribe_option

    @property
    def unsubscribe_option(self) -> bool:
        return self._unsubscribe_url is not None and self._unsubscribe_post

    def unsubscribe(self) -> bool:
        if not self.unsubscribe_option:
            log.warning("No one-click unsubscribe available for %s", self.subject)
            return False
        assert self._unsubscribe_url is not None
        resp = httpx.post(
            self._unsubscribe_url,
            data={"List-Unsubscribe": "One-Click"},
            timeout=30,
        )
        log.info(
            "Unsubscribe request to=%s status=%d",
            self._unsubscribe_url,
            resp.status_code,
        )
        return resp.is_success

    def archive(self) -> None:
        folder = self._mailbox._folder("\\Archive")
        self._mailbox._move(self._uid, folder)
        subject = (self.subject[:25] + "…") if len(self.subject) > 25 else self.subject
        log.human(
            "Archived email from **%s** — `%s` (uid %s)",
            self.from_address,
            subject,
            self._uid,
        )

    def spam(self) -> None:
        folder = self._mailbox._folder("\\Junk")
        self._mailbox._move(self._uid, folder)
        log.human("Marked as spam uid=%s subject=%s", self._uid, self.subject)

    def trash(self) -> None:
        folder = self._mailbox._folder("\\Trash")
        self._mailbox._move(self._uid, folder)
        log.human("Trashed email uid=%s subject=%s", self._uid, self.subject)

    def move_to(self, folder: str) -> None:
        self._mailbox._move(self._uid, folder)
        log.human("Moved email uid=%s to folder=%r subject=%s", self._uid, folder, self.subject)

    def draft_reply(self, contents: str) -> None:
        subject = _clean_header(self.subject)
        policy = EmailPolicy(utf8=True, max_line_length=998)
        reply = EmailMessage(policy=policy)
        reply["To"] = self.from_address
        reply["From"] = self.to_address
        reply["Subject"] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
        if self._message_id:
            reply["In-Reply-To"] = self._message_id
            refs = (
                f"{self._references} {self._message_id}".strip()
                if self._references
                else self._message_id
            )
            reply["References"] = refs
        reply.set_content(contents)

        self._mailbox._append_draft(reply.as_bytes())
        log.info(
            "Draft reply created to=%s subject=%s",
            self.from_address,
            reply["Subject"],
        )

    @staticmethod
    def _clean(text: str, html: str) -> str:
        if html:
            return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
        return text or ""

    def __repr__(self) -> str:
        return f"Email(from={self.from_address!r}, subject={self.subject!r})"


class Mailbox:
    def __init__(
        self,
        imap_server: str,
        imap_port: int,
        username: str,
        password: str,
    ) -> None:
        self._imap_server = imap_server
        self._imap_port = imap_port
        self._username = username
        self._password = password
        self.emails: list[Email] = []
        self._conn: IMAPToolsMailBox | None = None
        self._folders: dict[str, str] = {}

    def _ensure_connected(self) -> None:
        if self._conn is None:
            self._conn = IMAPToolsMailBox(self._imap_server, self._imap_port)
            self._conn.login(self._username, self._password)
            self._folders = _discover_folders(self._conn)
            log.info("IMAP connected to %s as %s", self._imap_server, self._username)
            log.info("Discovered folders: %s", self._folders)

    def inbox_message_ids(
        self,
        limit: int = 500,
        since: date | None = None,
    ) -> list[tuple[str, str]]:
        """Fetch (uid, message_id) pairs from the inbox using headers-only fetch.

        Returns a list of (uid, raw_message_id) tuples, newest first.
        message_id may be an empty string for malformed emails that lack a
        Message-ID header. When *since* is provided, only emails on or after
        that date are returned (IMAP SINCE is day-granularity).
        """
        self._ensure_connected()
        assert self._conn is not None
        criteria = AND(date_gte=since) if since else "ALL"
        messages = list(
            self._conn.fetch(
                criteria,
                headers_only=True,
                limit=limit,
                reverse=True,
                mark_seen=False,
            )
        )
        result = []
        for msg in messages:
            uid = msg.uid or ""
            mid = _clean_header(msg.headers.get("message-id", ("",))[0])
            result.append((uid, mid))
        return result

    def collect_emails(self, limit: int = 50) -> None:
        self._ensure_connected()
        assert self._conn is not None
        messages = list(self._conn.fetch(limit=limit, reverse=True, mark_seen=False))
        self.emails = sorted(
            [Email(msg, self) for msg in messages],
            key=lambda e: e.date,
            reverse=True,
        )
        log.info("Collected %d emails", len(self.emails))

    def get_email(self, uid: str) -> Email:
        self._ensure_connected()
        assert self._conn is not None
        messages = list(self._conn.fetch(AND(uid=uid), mark_seen=False))
        if not messages:
            raise ValueError(f"No email found with uid={uid}")
        return Email(messages[0], self)

    def _move(self, uid: str, folder: str) -> None:
        self._ensure_connected()
        assert self._conn is not None
        self._conn.move([uid], folder)

    def _folder(self, flag: str) -> str:
        self._ensure_connected()
        folder = self._folders.get(flag)
        if not folder:
            raise ValueError(f"No folder found with special-use flag {flag}")
        return folder

    def _append_draft(self, msg_bytes: bytes) -> None:
        folder = self._folder("\\Drafts")
        assert self._conn is not None
        self._conn.append(
            msg_bytes,
            folder=folder,
            dt=datetime.now(UTC),
            flag_set="\\Draft",
        )

    def disconnect(self) -> None:
        if self._conn:
            self._conn.logout()
            self._conn = None
            log.info("IMAP disconnected")

    def __enter__(self) -> Mailbox:
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()


def _discover_folders(conn: IMAPToolsMailBox) -> dict[str, str]:
    SPECIAL_USE_FLAGS = {"\\Archive", "\\Drafts", "\\Junk", "\\Sent", "\\Trash"}
    folders: dict[str, str] = {}
    for f in conn.folder.list():
        for flag in f.flags:
            if flag in SPECIAL_USE_FLAGS:
                folders[flag] = f.name
    return folders


def _clean_header(value: str) -> str:
    return " ".join(value.split())


def _parse_unsubscribe_url(headers: dict[str, tuple[str, ...]]) -> str | None:
    raw = headers.get("list-unsubscribe", ("",))
    value = " ".join(raw)
    urls = re.findall(r"<(https?://[^>]+)>", value)
    return urls[0] if urls else None


def _parse_received_date(headers: dict[str, tuple[str, ...]]) -> datetime | None:
    received = headers.get("received", ())
    if not received:
        return None
    first_hop = received[0]
    _, _, date_str = first_hop.rpartition(";")
    date_str = date_str.strip()
    if not date_str:
        return None
    try:
        dt: datetime = parsedate_to_datetime(date_str)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        log.warning("Failed to parse Received header date: %s", date_str)
        return None


def _count_attendees(attendees: Any) -> int:
    if attendees is None:
        return 0
    if isinstance(attendees, list):
        return len(attendees)
    return 1


def _extract_partstat(method: str, attendees: Any) -> str | None:
    if method != "reply":
        return None
    first = attendees[0] if isinstance(attendees, list) else attendees
    if first is None:
        return None
    raw = str(first.params.get("partstat", "")).lower()
    return raw or None


def _extract_vevent(component: Any, method: str) -> dict[str, Any]:
    """Build a calendar event dict from a VEVENT component."""
    dtstart = component.get("dtstart")
    dtend = component.get("dtend")
    sequence = int(component.get("sequence", 0))
    attendees = component.get("attendee")
    return {
        "start": dtstart.dt.isoformat() if dtstart else None,
        "end": dtend.dt.isoformat() if dtend else None,
        "guest_count": _count_attendees(attendees),
        "method": method,
        "is_update": method == "request" and sequence > 0,
        "partstat": _extract_partstat(method, attendees),
    }


def _parse_ical(att: Any) -> dict[str, Any] | None:
    """Parse a single iCal attachment, returning event data or None."""
    try:
        cal = Calendar.from_ical(att.payload)
    except Exception:
        log.warning("Failed to parse calendar attachment")
        return None
    raw_method = cal.get("method")  # type: ignore[no-untyped-call]
    method = str(raw_method).lower() if raw_method else "request"
    for component in cal.walk():
        if component.name == "VEVENT":
            return _extract_vevent(component, method)
    return None


def _parse_calendar(attachments: Any) -> dict[str, Any] | None:
    """Extract calendar event data from email attachments.

    Returns a dict with:
      start, end    — ISO strings (or None)
      guest_count   — number of attendees
      method        — iTIP method lowercased: "request", "cancel", "reply", etc.
      is_update     — True when method=request and SEQUENCE > 0
      partstat      — reply status lowercased for METHOD:REPLY emails:
                      "accepted", "declined", "tentative", or None

    Returns None if the email has no calendar attachment.
    """
    for att in attachments:
        if att.content_type not in ("text/calendar", "application/ics"):
            continue
        return _parse_ical(att)
    return None


_CALENDAR_CONTENT_TYPES = frozenset({"text/calendar", "application/ics"})


def _has_non_calendar_attachments(attachments: Any) -> bool:
    return any(att.content_type not in _CALENDAR_CONTENT_TYPES for att in attachments)


def _parse_auth_results(headers: dict[str, tuple[str, ...]]) -> tuple[bool, bool, bool]:
    auth = headers.get("authentication-results", ("",))
    auth_lower = " ".join(auth).lower()
    return (
        "spf=pass" in auth_lower,
        "dkim=pass" in auth_lower,
        "dmarc=pass" in auth_lower,
    )
