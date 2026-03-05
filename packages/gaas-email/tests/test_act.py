from unittest.mock import MagicMock

from gaas_email.platforms.inbox.act import _execute_action
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
