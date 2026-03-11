from pathlib import Path
from unittest.mock import MagicMock, patch

from gaas_email.platforms.inbox.act import (
    _execute_action,
    _is_irreversible,
    _unwrap_yolo,
    handle,
)
from gaas_email.platforms.inbox.const import SIMPLE_ACTIONS


class TestExecuteAction:
    def _mock_email(self):
        email = MagicMock()
        email.archive = MagicMock()
        email.spam = MagicMock()
        email.trash = MagicMock()
        email.unsubscribe = MagicMock()
        email.draft_reply = MagicMock()
        email.move_to = MagicMock()
        return email

    def test_archive_calls_email_archive(self):
        email = self._mock_email()
        _execute_action(email, "archive")
        email.archive.assert_called_once()

    def test_spam_calls_email_spam(self):
        email = self._mock_email()
        _execute_action(email, "spam")
        email.spam.assert_called_once()

    def test_trash_calls_email_trash(self):
        email = self._mock_email()
        _execute_action(email, "trash")
        email.trash.assert_called_once()

    def test_unsubscribe_calls_email_unsubscribe(self):
        email = self._mock_email()
        _execute_action(email, "unsubscribe")
        email.unsubscribe.assert_called_once()

    def test_draft_reply_calls_with_content(self):
        email = self._mock_email()
        _execute_action(email, {"draft_reply": "Thanks, I'll review."})
        email.draft_reply.assert_called_once_with("Thanks, I'll review.")

    def test_move_to_calls_with_folder(self):
        email = self._mock_email()
        _execute_action(email, {"move_to": "Newsletters"})
        email.move_to.assert_called_once_with("Newsletters")

    def test_move_to_nested_folder(self):
        email = self._mock_email()
        _execute_action(email, {"move_to": "Work/Stripe"})
        email.move_to.assert_called_once_with("Work/Stripe")

    def test_unknown_string_action_skipped(self):
        email = self._mock_email()
        _execute_action(email, "delete_everything")
        email.archive.assert_not_called()
        email.spam.assert_not_called()
        email.trash.assert_not_called()
        email.unsubscribe.assert_not_called()
        email.draft_reply.assert_not_called()

    def test_unknown_dict_action_skipped(self):
        email = self._mock_email()
        _execute_action(email, {"send_email": "to everyone"})
        email.draft_reply.assert_not_called()

    def test_simple_actions_set_is_bounded(self):
        """The set of simple actions is explicitly defined and should not grow
        without deliberate review of reversibility tiers."""
        assert {"archive", "spam", "trash", "unsubscribe"} == SIMPLE_ACTIONS


class TestHandle:
    """Tests for handle() — the orchestration function that connects to IMAP,
    fetches an email by UID, runs actions, and syncs the note store."""

    def _mock_email(self):
        email = MagicMock()
        email._message_id = "<test@example.com>"
        email.archive = MagicMock()
        email.spam = MagicMock()
        email.trash = MagicMock()
        email.unsubscribe = MagicMock()
        email.draft_reply = MagicMock()
        email.move_to = MagicMock()
        return email

    def _make_task(self, actions, provenance=None):
        task = {
            "id": "task-001",
            "created_at": "2026-03-08T00:00:00",
            "status": "active",
            "priority": 5,
            "payload": {
                "type": "email.inbox.act",
                "integration": "email.personal",
                "uid": "12345",
                "actions": actions,
            },
        }
        if provenance is not None:
            task["provenance"] = provenance
        return task

    def _mock_integration(self):
        integration = MagicMock()
        integration.name = "personal"
        integration.imap_server = "imap.example.com"
        integration.imap_port = 993
        integration.username = "user@example.com"
        integration.password = "secret"
        return integration

    @patch("gaas_email.platforms.inbox.act.EmailStore")
    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_handle_dispatches_actions(self, MockMailbox, mock_runtime, MockStore):
        email = self._mock_email()
        mb = MagicMock()
        mb.get_email.return_value = email
        mb.__enter__ = MagicMock(return_value=mb)
        mb.__exit__ = MagicMock(return_value=False)
        MockMailbox.return_value = mb

        mock_runtime.get_integration.return_value = self._mock_integration()
        mock_runtime.get_notes_dir.return_value = None

        task = self._make_task(["archive", "spam"])
        handle(task)

        MockMailbox.assert_called_once_with(
            imap_server="imap.example.com",
            imap_port=993,
            username="user@example.com",
            password="secret",
        )
        mb.get_email.assert_called_once_with("12345")
        email.archive.assert_called_once()
        email.spam.assert_called_once()

    @patch("gaas_email.platforms.inbox.act.EmailStore")
    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_handle_store_sync_folder_moves(self, MockMailbox, mock_runtime, MockStore):
        email = self._mock_email()
        mb = MagicMock()
        mb.get_email.return_value = email
        mb.__enter__ = MagicMock(return_value=mb)
        mb.__exit__ = MagicMock(return_value=False)
        MockMailbox.return_value = mb

        mock_runtime.get_integration.return_value = self._mock_integration()
        mock_runtime.get_notes_dir.return_value = Path("/notes")

        store_instance = MagicMock()
        MockStore.return_value = store_instance

        for action in ["archive", "spam", "trash", {"move_to": "Receipts"}]:
            store_instance.reset_mock()
            task = self._make_task([action])
            handle(task)
            store_instance.move_to_subdir.assert_called_once_with(
                "<test@example.com>", "synced"
            )

    @patch("gaas_email.platforms.inbox.act.EmailStore")
    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_handle_store_no_sync_for_non_folder_actions(
        self, MockMailbox, mock_runtime, MockStore
    ):
        email = self._mock_email()
        mb = MagicMock()
        mb.get_email.return_value = email
        mb.__enter__ = MagicMock(return_value=mb)
        mb.__exit__ = MagicMock(return_value=False)
        MockMailbox.return_value = mb

        mock_runtime.get_integration.return_value = self._mock_integration()
        mock_runtime.get_notes_dir.return_value = Path("/notes")

        store_instance = MagicMock()
        MockStore.return_value = store_instance

        for action in ["unsubscribe", {"draft_reply": "Thanks!"}]:
            store_instance.reset_mock()
            task = self._make_task([action])
            handle(task)
            store_instance.move_to_subdir.assert_not_called()

    @patch("gaas_email.platforms.inbox.act.EmailStore")
    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_handle_no_notes_dir(self, MockMailbox, mock_runtime, MockStore):
        email = self._mock_email()
        mb = MagicMock()
        mb.get_email.return_value = email
        mb.__enter__ = MagicMock(return_value=mb)
        mb.__exit__ = MagicMock(return_value=False)
        MockMailbox.return_value = mb

        mock_runtime.get_integration.return_value = self._mock_integration()
        mock_runtime.get_notes_dir.return_value = None

        task = self._make_task(["archive"])
        handle(task)

        MockStore.assert_not_called()
        email.archive.assert_called_once()

    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_handle_imap_error_propagates(self, MockMailbox, mock_runtime):
        mb = MagicMock()
        mb.get_email.side_effect = Exception("IMAP connection lost")
        mb.__enter__ = MagicMock(return_value=mb)
        mb.__exit__ = MagicMock(return_value=False)
        MockMailbox.return_value = mb

        mock_runtime.get_integration.return_value = self._mock_integration()
        mock_runtime.get_notes_dir.return_value = None

        task = self._make_task(["archive"])
        try:
            handle(task)
            raise AssertionError("Expected exception to propagate")
        except Exception as e:
            assert str(e) == "IMAP connection lost"

    @patch("gaas_email.platforms.inbox.act.EmailStore")
    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_handle_provenance_defaults_to_unknown(
        self, MockMailbox, mock_runtime, MockStore
    ):
        email = self._mock_email()
        mb = MagicMock()
        mb.get_email.return_value = email
        mb.__enter__ = MagicMock(return_value=mb)
        mb.__exit__ = MagicMock(return_value=False)
        MockMailbox.return_value = mb

        mock_runtime.get_integration.return_value = self._mock_integration()
        mock_runtime.get_notes_dir.return_value = None

        task = self._make_task(["archive"])
        assert "provenance" not in task

        # Should not raise — provenance defaults to "unknown" via .get()
        handle(task)


class TestRuntimeProvenanceCheck:
    """Runtime defense-in-depth: irreversible actions are blocked when
    provenance is 'llm' or 'hybrid', unless explicitly !yolo-tagged."""

    def _mock_email(self):
        email = MagicMock()
        email._message_id = "<test@example.com>"
        email.archive = MagicMock()
        email.spam = MagicMock()
        email.trash = MagicMock()
        email.unsubscribe = MagicMock()
        email.draft_reply = MagicMock()
        email.move_to = MagicMock()
        return email

    def _make_task(self, actions, provenance=None):
        task = {
            "id": "task-001",
            "created_at": "2026-03-08T00:00:00",
            "status": "active",
            "priority": 5,
            "payload": {
                "type": "email.inbox.act",
                "integration": "email.personal",
                "uid": "12345",
                "actions": actions,
            },
        }
        if provenance is not None:
            task["provenance"] = provenance
        return task

    def _mock_integration(self):
        integration = MagicMock()
        integration.name = "personal"
        integration.imap_server = "imap.example.com"
        integration.imap_port = 993
        integration.username = "user@example.com"
        integration.password = "secret"
        return integration

    def _setup_mailbox(self, MockMailbox, mock_runtime, email):
        mb = MagicMock()
        mb.get_email.return_value = email
        mb.__enter__ = MagicMock(return_value=mb)
        mb.__exit__ = MagicMock(return_value=False)
        MockMailbox.return_value = mb
        mock_runtime.get_integration.return_value = self._mock_integration()
        mock_runtime.get_notes_dir.return_value = None

    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_irreversible_action_blocked_from_llm_provenance(
        self, MockMailbox, mock_runtime
    ):
        """Unsubscribe with provenance=llm is skipped at runtime."""
        email = self._mock_email()
        self._setup_mailbox(MockMailbox, mock_runtime, email)

        task = self._make_task(["unsubscribe"], provenance="llm")
        handle(task)

        email.unsubscribe.assert_not_called()

    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_irreversible_action_blocked_from_hybrid_provenance(
        self, MockMailbox, mock_runtime
    ):
        """Unsubscribe with provenance=hybrid is skipped at runtime."""
        email = self._mock_email()
        self._setup_mailbox(MockMailbox, mock_runtime, email)

        task = self._make_task(["unsubscribe"], provenance="hybrid")
        handle(task)

        email.unsubscribe.assert_not_called()

    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_irreversible_action_allowed_from_rule_provenance(
        self, MockMailbox, mock_runtime
    ):
        """Unsubscribe with provenance=rule executes normally."""
        email = self._mock_email()
        self._setup_mailbox(MockMailbox, mock_runtime, email)

        task = self._make_task(["unsubscribe"], provenance="rule")
        handle(task)

        email.unsubscribe.assert_called_once()

    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_reversible_actions_execute_alongside_blocked_irreversible(
        self, MockMailbox, mock_runtime
    ):
        """Archive executes normally even when unsubscribe is blocked."""
        email = self._mock_email()
        self._setup_mailbox(MockMailbox, mock_runtime, email)

        task = self._make_task(["archive", "unsubscribe"], provenance="llm")
        handle(task)

        email.archive.assert_called_once()
        email.unsubscribe.assert_not_called()

    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_yolo_irreversible_action_executes_from_llm_provenance(
        self, MockMailbox, mock_runtime
    ):
        """!yolo-wrapped unsubscribe executes even with provenance=llm."""
        email = self._mock_email()
        self._setup_mailbox(MockMailbox, mock_runtime, email)

        task = self._make_task(
            [{"!yolo": "unsubscribe"}], provenance="llm"
        )
        handle(task)

        email.unsubscribe.assert_called_once()

    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_blocked_action_logs_warning(self, MockMailbox, mock_runtime, caplog):
        """Blocked irreversible action emits a warning log."""
        import logging

        email = self._mock_email()
        self._setup_mailbox(MockMailbox, mock_runtime, email)

        task = self._make_task(["unsubscribe"], provenance="llm")
        with caplog.at_level(logging.WARNING):
            handle(task)

        assert any("BLOCKED" in msg and "unsubscribe" in msg for msg in caplog.messages)

    @patch("gaas_email.platforms.inbox.act.runtime")
    @patch("gaas_email.mail.Mailbox")
    def test_irreversible_action_allowed_from_unknown_provenance(
        self, MockMailbox, mock_runtime
    ):
        """provenance=unknown (default) does not block — only llm/hybrid are blocked."""
        email = self._mock_email()
        self._setup_mailbox(MockMailbox, mock_runtime, email)

        task = self._make_task(["unsubscribe"])  # no provenance → "unknown"
        handle(task)

        email.unsubscribe.assert_called_once()


class TestUnwrapYolo:
    def test_plain_action_not_yolo(self):
        action, yolo = _unwrap_yolo("archive")
        assert action == "archive"
        assert yolo is False

    def test_yolo_wrapped_action(self):
        action, yolo = _unwrap_yolo({"!yolo": "unsubscribe"})
        assert action == "unsubscribe"
        assert yolo is True

    def test_regular_dict_not_yolo(self):
        action, yolo = _unwrap_yolo({"draft_reply": "Hi"})
        assert action == {"draft_reply": "Hi"}
        assert yolo is False


class TestIsIrreversible:
    def test_unsubscribe_is_irreversible(self):
        assert _is_irreversible("unsubscribe") is True

    def test_archive_is_reversible(self):
        assert _is_irreversible("archive") is False

    def test_dict_with_irreversible_key(self):
        assert _is_irreversible({"unsubscribe": True}) is True

    def test_dict_without_irreversible_key(self):
        assert _is_irreversible({"draft_reply": "Hi"}) is False
