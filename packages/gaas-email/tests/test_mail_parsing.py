from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock

from gaas_email.mail import (
    Email,
    _clean_header,
    _parse_auth_results,
    _parse_calendar,
    _parse_received_date,
    _parse_unsubscribe_url,
)


def _make_email(
    subject: str = "Hello",
    from_address: str = "sender@example.com",
    in_reply_to: str = "",
    list_unsubscribe: str = "",
    list_unsubscribe_post: str = "",
    flags: tuple = (),
    attachments: list | None = None,
) -> Email:
    """Build a minimal Email object for testing flag properties."""
    msg = MagicMock()
    msg.uid = "1"
    msg.headers = {
        "message-id": ("<test@example.com>",),
        "references": (),
        "in-reply-to": (in_reply_to,),
        "received": (),
        "authentication-results": ("none",),
        "list-unsubscribe": (list_unsubscribe,) if list_unsubscribe else (),
        "list-unsubscribe-post": (list_unsubscribe_post,) if list_unsubscribe_post else (),
    }
    msg.from_ = from_address
    msg.from_values = None
    msg.to = ["recipient@example.com"]
    msg.subject = subject
    msg.date = datetime.now(UTC)
    msg.text = ""
    msg.html = ""
    msg.flags = flags
    msg.attachments = attachments if attachments is not None else []
    return Email(msg, MagicMock())


# ---------------------------------------------------------------------------
# _parse_auth_results
# ---------------------------------------------------------------------------


class TestParseAuthResults:
    def test_all_pass(self):
        headers = {
            "authentication-results": (
                "mx.example.com; spf=pass; dkim=pass; dmarc=pass",
            )
        }
        spf, dkim, dmarc = _parse_auth_results(headers)
        assert spf is True
        assert dkim is True
        assert dmarc is True

    def test_all_fail(self):
        headers = {
            "authentication-results": (
                "mx.example.com; spf=fail; dkim=fail; dmarc=fail",
            )
        }
        spf, dkim, dmarc = _parse_auth_results(headers)
        assert spf is False
        assert dkim is False
        assert dmarc is False

    def test_partial_pass(self):
        headers = {
            "authentication-results": (
                "mx.example.com; spf=pass; dkim=fail; dmarc=pass",
            )
        }
        spf, dkim, dmarc = _parse_auth_results(headers)
        assert spf is True
        assert dkim is False
        assert dmarc is True

    def test_empty_headers(self):
        spf, dkim, dmarc = _parse_auth_results({})
        assert spf is False
        assert dkim is False
        assert dmarc is False


# ---------------------------------------------------------------------------
# _parse_unsubscribe_url
# ---------------------------------------------------------------------------


class TestParseUnsubscribeUrl:
    def test_extracts_http_url(self):
        headers = {
            "list-unsubscribe": ("<https://example.com/unsubscribe?id=123>",)
        }
        url = _parse_unsubscribe_url(headers)
        assert url == "https://example.com/unsubscribe?id=123"

    def test_extracts_from_multiple_options(self):
        headers = {
            "list-unsubscribe": (
                "<mailto:unsub@example.com>, <https://example.com/unsub>",
            )
        }
        url = _parse_unsubscribe_url(headers)
        assert url == "https://example.com/unsub"

    def test_returns_none_when_missing(self):
        assert _parse_unsubscribe_url({}) is None

    def test_returns_none_when_no_http_url(self):
        headers = {"list-unsubscribe": ("<mailto:unsub@example.com>",)}
        assert _parse_unsubscribe_url(headers) is None


# ---------------------------------------------------------------------------
# _parse_received_date
# ---------------------------------------------------------------------------


class TestParseReceivedDate:
    def test_parses_valid_date(self):
        headers = {
            "received": (
                "from mail.example.com by mx.google.com; Tue, 15 Feb 2025 10:30:00 +0000",
            )
        }
        dt = _parse_received_date(headers)
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 2
        assert dt.day == 15

    def test_returns_none_when_missing(self):
        assert _parse_received_date({}) is None

    def test_returns_none_on_malformed(self):
        headers = {"received": ("completely garbage data without semicolon",)}
        # No semicolon means no date part to parse
        assert _parse_received_date(headers) is None


# ---------------------------------------------------------------------------
# _parse_calendar
# ---------------------------------------------------------------------------


def _make_attachment(content_type: str, payload: bytes):
    return SimpleNamespace(content_type=content_type, payload=payload)


_VALID_ICS = (
    b"BEGIN:VCALENDAR\r\n"
    b"METHOD:REQUEST\r\n"
    b"BEGIN:VEVENT\r\n"
    b"DTSTART:20260301T140000Z\r\n"
    b"DTEND:20260301T150000Z\r\n"
    b"SUMMARY:Team Standup\r\n"
    b"ATTENDEE:mailto:alice@example.com\r\n"
    b"ATTENDEE:mailto:bob@example.com\r\n"
    b"ATTENDEE:mailto:charlie@example.com\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)

_CANCEL_ICS = (
    b"BEGIN:VCALENDAR\r\n"
    b"METHOD:CANCEL\r\n"
    b"BEGIN:VEVENT\r\n"
    b"DTSTART:20260301T140000Z\r\n"
    b"DTEND:20260301T150000Z\r\n"
    b"SEQUENCE:1\r\n"
    b"SUMMARY:Team Standup\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)

_UPDATE_ICS = (
    b"BEGIN:VCALENDAR\r\n"
    b"METHOD:REQUEST\r\n"
    b"BEGIN:VEVENT\r\n"
    b"DTSTART:20260301T150000Z\r\n"
    b"DTEND:20260301T160000Z\r\n"
    b"SEQUENCE:2\r\n"
    b"SUMMARY:Team Standup (rescheduled)\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)

_REPLY_ACCEPTED_ICS = (
    b"BEGIN:VCALENDAR\r\n"
    b"METHOD:REPLY\r\n"
    b"BEGIN:VEVENT\r\n"
    b"DTSTART:20260301T140000Z\r\n"
    b"DTEND:20260301T150000Z\r\n"
    b"ATTENDEE;PARTSTAT=ACCEPTED:mailto:alice@example.com\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)

_REPLY_DECLINED_ICS = (
    b"BEGIN:VCALENDAR\r\n"
    b"METHOD:REPLY\r\n"
    b"BEGIN:VEVENT\r\n"
    b"DTSTART:20260301T140000Z\r\n"
    b"DTEND:20260301T150000Z\r\n"
    b"ATTENDEE;PARTSTAT=DECLINED:mailto:alice@example.com\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)

_REPLY_TENTATIVE_ICS = (
    b"BEGIN:VCALENDAR\r\n"
    b"METHOD:REPLY\r\n"
    b"BEGIN:VEVENT\r\n"
    b"DTSTART:20260301T140000Z\r\n"
    b"DTEND:20260301T150000Z\r\n"
    b"ATTENDEE;PARTSTAT=TENTATIVE:mailto:alice@example.com\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)


class TestParseCalendar:
    def test_valid_event(self):
        att = _make_attachment("text/calendar", _VALID_ICS)
        result = _parse_calendar([att])
        assert result is not None
        assert result["start"] == "2026-03-01T14:00:00+00:00"
        assert result["end"] == "2026-03-01T15:00:00+00:00"
        assert result["guest_count"] == 3

    def test_single_attendee(self):
        ics = (
            b"BEGIN:VCALENDAR\r\n"
            b"BEGIN:VEVENT\r\n"
            b"DTSTART:20260301T140000Z\r\n"
            b"DTEND:20260301T150000Z\r\n"
            b"ATTENDEE:mailto:alice@example.com\r\n"
            b"END:VEVENT\r\n"
            b"END:VCALENDAR\r\n"
        )
        att = _make_attachment("text/calendar", ics)
        result = _parse_calendar([att])
        assert result is not None
        assert result["guest_count"] == 1

    def test_no_attendees(self):
        ics = (
            b"BEGIN:VCALENDAR\r\n"
            b"BEGIN:VEVENT\r\n"
            b"DTSTART:20260301T140000Z\r\n"
            b"DTEND:20260301T150000Z\r\n"
            b"END:VEVENT\r\n"
            b"END:VCALENDAR\r\n"
        )
        att = _make_attachment("text/calendar", ics)
        result = _parse_calendar([att])
        assert result is not None
        assert result["guest_count"] == 0

    def test_no_calendar_attachment(self):
        att = _make_attachment("application/pdf", b"not a calendar")
        assert _parse_calendar([att]) is None

    def test_empty_attachments(self):
        assert _parse_calendar([]) is None

    def test_no_vevent(self):
        ics = (
            b"BEGIN:VCALENDAR\r\n"
            b"BEGIN:VTODO\r\n"
            b"SUMMARY:Do something\r\n"
            b"END:VTODO\r\n"
            b"END:VCALENDAR\r\n"
        )
        att = _make_attachment("text/calendar", ics)
        assert _parse_calendar([att]) is None

    def test_no_dtend(self):
        ics = (
            b"BEGIN:VCALENDAR\r\n"
            b"BEGIN:VEVENT\r\n"
            b"DTSTART;VALUE=DATE:20260301\r\n"
            b"SUMMARY:All day event\r\n"
            b"END:VEVENT\r\n"
            b"END:VCALENDAR\r\n"
        )
        att = _make_attachment("text/calendar", ics)
        result = _parse_calendar([att])
        assert result is not None
        assert result["end"] is None

    def test_malformed_ics_returns_none(self):
        att = _make_attachment("text/calendar", b"this is not valid ical data")
        assert _parse_calendar([att]) is None

    def test_application_ics_content_type(self):
        """Google Calendar sends .ics files as application/ics, not text/calendar."""
        att = _make_attachment("application/ics", _VALID_ICS)
        result = _parse_calendar([att])
        assert result is not None
        assert result["guest_count"] == 3

    def test_invitation_method_and_defaults(self):
        att = _make_attachment("text/calendar", _VALID_ICS)
        result = _parse_calendar([att])
        assert result["method"] == "request"
        assert result["is_update"] is False
        assert result["partstat"] is None

    def test_cancelled_event(self):
        att = _make_attachment("text/calendar", _CANCEL_ICS)
        result = _parse_calendar([att])
        assert result is not None
        assert result["method"] == "cancel"
        assert result["is_update"] is False
        assert result["partstat"] is None

    def test_updated_event(self):
        """REQUEST with SEQUENCE > 0 is an update to an existing event."""
        att = _make_attachment("text/calendar", _UPDATE_ICS)
        result = _parse_calendar([att])
        assert result is not None
        assert result["method"] == "request"
        assert result["is_update"] is True

    def test_reply_accepted(self):
        att = _make_attachment("text/calendar", _REPLY_ACCEPTED_ICS)
        result = _parse_calendar([att])
        assert result is not None
        assert result["method"] == "reply"
        assert result["partstat"] == "accepted"

    def test_reply_declined(self):
        att = _make_attachment("text/calendar", _REPLY_DECLINED_ICS)
        result = _parse_calendar([att])
        assert result is not None
        assert result["method"] == "reply"
        assert result["partstat"] == "declined"

    def test_reply_tentative(self):
        att = _make_attachment("text/calendar", _REPLY_TENTATIVE_ICS)
        result = _parse_calendar([att])
        assert result is not None
        assert result["method"] == "reply"
        assert result["partstat"] == "tentative"

    def test_skips_non_calendar_attachments(self):
        pdf = _make_attachment("application/pdf", b"pdf content")
        cal = _make_attachment("text/calendar", _VALID_ICS)
        result = _parse_calendar([pdf, cal])
        assert result is not None
        assert result["guest_count"] == 3


# ---------------------------------------------------------------------------
# _clean_header
# ---------------------------------------------------------------------------


class TestCleanHeader:
    def test_collapses_whitespace(self):
        assert _clean_header("  hello   world  ") == "hello world"

    def test_handles_newlines_and_tabs(self):
        assert _clean_header("hello\n\tworld") == "hello world"

    def test_empty_string(self):
        assert _clean_header("") == ""


# ---------------------------------------------------------------------------
# Email.is_reply
# ---------------------------------------------------------------------------


class TestIsReply:
    def test_true_when_in_reply_to_present(self):
        email = _make_email(in_reply_to="<original@example.com>")
        assert email.is_reply is True

    def test_false_when_in_reply_to_absent(self):
        email = _make_email(in_reply_to="")
        assert email.is_reply is False


# ---------------------------------------------------------------------------
# Email.is_forward
# ---------------------------------------------------------------------------


class TestIsForward:
    def test_fwd_prefix(self):
        assert _make_email("Fwd: Meeting notes").is_forward is True

    def test_fw_prefix(self):
        assert _make_email("FW: Meeting notes").is_forward is True

    def test_fw_lowercase(self):
        assert _make_email("fw: Meeting notes").is_forward is True

    def test_bracketed_fwd(self):
        assert _make_email("[Fwd: old thread]").is_forward is True

    def test_regular_subject_not_forward(self):
        assert _make_email("Meeting notes").is_forward is False

    def test_subject_containing_fwd_not_prefix(self):
        assert _make_email("Notes about fwd: policy").is_forward is False


# ---------------------------------------------------------------------------
# Email.is_unsubscribable
# ---------------------------------------------------------------------------


class TestIsUnsubscribable:
    def test_true_when_both_headers_present(self):
        email = _make_email(
            list_unsubscribe="<https://example.com/unsub?id=123>",
            list_unsubscribe_post="List-Unsubscribe=One-Click",
        )
        assert email.is_unsubscribable is True

    def test_false_without_post_header(self):
        """RFC 8058 requires List-Unsubscribe-Post — URL alone is not enough."""
        email = _make_email(list_unsubscribe="<https://example.com/unsub?id=123>")
        assert email.is_unsubscribable is False

    def test_false_without_any_headers(self):
        assert _make_email().is_unsubscribable is False


# ---------------------------------------------------------------------------
# Email.is_read / is_starred / is_answered
# ---------------------------------------------------------------------------


class TestImapFlags:
    def test_is_read_when_seen_flag_set(self):
        assert _make_email(flags=("\\Seen",)).is_read is True

    def test_is_not_read_without_seen_flag(self):
        assert _make_email(flags=()).is_read is False

    def test_is_starred_when_flagged(self):
        assert _make_email(flags=("\\Flagged",)).is_starred is True

    def test_is_not_starred_without_flag(self):
        assert _make_email(flags=()).is_starred is False

    def test_is_answered_when_flag_set(self):
        assert _make_email(flags=("\\Answered",)).is_answered is True

    def test_is_not_answered_without_flag(self):
        assert _make_email(flags=()).is_answered is False

    def test_multiple_flags_coexist(self):
        email = _make_email(flags=("\\Seen", "\\Flagged", "\\Answered"))
        assert email.is_read is True
        assert email.is_starred is True
        assert email.is_answered is True


# ---------------------------------------------------------------------------
# Email.has_attachments
# ---------------------------------------------------------------------------


class TestHasAttachments:
    def _att(self, content_type: str):
        from types import SimpleNamespace
        return SimpleNamespace(content_type=content_type)

    def test_true_with_pdf_attachment(self):
        email = _make_email(attachments=[self._att("application/pdf")])
        assert email.has_attachments is True

    def test_true_with_image_attachment(self):
        email = _make_email(attachments=[self._att("image/png")])
        assert email.has_attachments is True

    def test_false_with_no_attachments(self):
        assert _make_email(attachments=[]).has_attachments is False

    def test_false_when_only_calendar_attachment(self):
        """Calendar attachments are not counted — is_calendar_event covers those."""
        email = _make_email(attachments=[
            self._att("text/calendar"),
            self._att("application/ics"),
        ])
        assert email.has_attachments is False

    def test_true_when_calendar_plus_other_attachment(self):
        email = _make_email(attachments=[
            self._att("text/calendar"),
            self._att("application/pdf"),
        ])
        assert email.has_attachments is True


# ---------------------------------------------------------------------------
# Email.root_domain
# ---------------------------------------------------------------------------


class TestRootDomain:
    def test_simple_domain(self):
        email = _make_email(from_address="user@example.com")
        assert email.root_domain == "example.com"

    def test_subdomain(self):
        email = _make_email(from_address="user@mail.company.com")
        assert email.root_domain == "company.com"

    def test_multi_part_tld(self):
        email = _make_email(from_address="no-reply@mail.company.co.uk")
        assert email.root_domain == "company.co.uk"
