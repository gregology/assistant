"""Tests for the create_issue service handler."""

from unittest.mock import patch

from assistant_github.services.create_issue import handle


def _make_task(inputs: dict) -> dict:
    return {
        "id": "1_20260320T100000Z_abc--def--service.github.create_issue",
        "payload": {
            "type": "service.github.create_issue",
            "inputs": inputs,
        },
    }


class TestCreateIssueHandler:
    def test_creates_issue_and_returns_result(self):
        task = _make_task(
            {
                "repo": "myorg/myrepo",
                "title": "Bug report",
                "body": "Steps to reproduce",
            }
        )
        with patch("assistant_github.services.create_issue.GitHubClient") as MockClient:
            MockClient.return_value.create_issue.return_value = {
                "number": 42,
                "url": "https://github.com/myorg/myrepo/issues/42",
            }
            result = handle(task)

        assert result["number"] == 42
        assert result["url"] == "https://github.com/myorg/myrepo/issues/42"
        assert "42" in result["text"]
        assert result["org"] == "myorg"
        assert result["repo"] == "myrepo"

        MockClient.return_value.create_issue.assert_called_once_with(
            "myorg",
            "myrepo",
            "Bug report",
            "Steps to reproduce",
        )

    def test_missing_repo(self):
        task = _make_task({"title": "Bug"})
        result = handle(task)
        assert "Missing required fields" in result["text"]

    def test_missing_title(self):
        task = _make_task({"repo": "org/repo"})
        result = handle(task)
        assert "Missing required fields" in result["text"]

    def test_empty_inputs(self):
        task = _make_task({})
        result = handle(task)
        assert "Missing required fields" in result["text"]

    def test_invalid_repo_format(self):
        task = _make_task({"repo": "noslash", "title": "Bug"})
        result = handle(task)
        assert "Invalid repo format" in result["text"]

    def test_body_defaults_to_empty(self):
        task = _make_task({"repo": "org/repo", "title": "Bug"})
        with patch("assistant_github.services.create_issue.GitHubClient") as MockClient:
            MockClient.return_value.create_issue.return_value = {
                "number": 1,
                "url": "",
            }
            handle(task)
        MockClient.return_value.create_issue.assert_called_once_with(
            "org",
            "repo",
            "Bug",
            "",
        )
