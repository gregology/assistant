from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from imap_tools import AND

from gaas_email.mail import Mailbox
from gaas_email.platforms.inbox.check import _parse_window_days


# -- _parse_window_days -------------------------------------------------------


class TestParseWindowDays:
    def test_simple_days(self):
        assert _parse_window_days("7d") == 7
        assert _parse_window_days("30d") == 30
        assert _parse_window_days("1d") == 1

    def test_whitespace_tolerance(self):
        assert _parse_window_days("  7d  ") == 7
        assert _parse_window_days("30 d") == 30

    def test_case_insensitive(self):
        assert _parse_window_days("7D") == 7

    def test_rejects_hours(self):
        with pytest.raises(ValueError, match="Invalid window format"):
            _parse_window_days("24h")

    def test_rejects_minutes(self):
        with pytest.raises(ValueError, match="Invalid window format"):
            _parse_window_days("60m")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError, match="Invalid window format"):
            _parse_window_days("abc")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid window format"):
            _parse_window_days("")


# -- inbox_message_ids ---------------------------------------------------------


def _make_mailbox() -> Mailbox:
    """Create a Mailbox with a mocked IMAP connection."""
    mb = Mailbox(
        imap_server="imap.example.com",
        imap_port=993,
        username="user@example.com",
        password="secret",
    )
    mb._conn = MagicMock()
    mb._folders = {}
    return mb


def _fake_msg(uid: str, message_id: str = "") -> MagicMock:
    msg = MagicMock()
    msg.uid = uid
    msg.headers = {"message-id": (message_id,)} if message_id else {"message-id": ("",)}
    return msg


class TestInboxMessageIds:
    def test_reverse_true_by_default(self):
        mb = _make_mailbox()
        mb._conn.fetch.return_value = []

        mb.inbox_message_ids(limit=10)

        _, kwargs = mb._conn.fetch.call_args
        assert kwargs["reverse"] is True

    def test_criteria_all_when_no_since(self):
        mb = _make_mailbox()
        mb._conn.fetch.return_value = []

        mb.inbox_message_ids(limit=10)

        args, _ = mb._conn.fetch.call_args
        assert args[0] == "ALL"

    def test_criteria_date_gte_when_since_provided(self):
        mb = _make_mailbox()
        mb._conn.fetch.return_value = []
        since = date(2025, 1, 15)

        mb.inbox_message_ids(limit=10, since=since)

        args, _ = mb._conn.fetch.call_args
        expected = AND(date_gte=since)
        assert str(args[0]) == str(expected)

    def test_returns_uid_message_id_pairs(self):
        mb = _make_mailbox()
        mb._conn.fetch.return_value = [
            _fake_msg("100", "<msg1@example.com>"),
            _fake_msg("101", "<msg2@example.com>"),
        ]

        result = mb.inbox_message_ids(limit=10)

        assert result == [("100", "<msg1@example.com>"), ("101", "<msg2@example.com>")]

    def test_empty_message_id_preserved(self):
        mb = _make_mailbox()
        mb._conn.fetch.return_value = [_fake_msg("200", "")]

        result = mb.inbox_message_ids(limit=5)

        assert result == [("200", "")]
