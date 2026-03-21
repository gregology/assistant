"""Tests for assistant_github.client — GitHub API client parsing and search logic.

Tests focus on deterministic parsing and search deduplication, not subprocess
calls (which require gh CLI to be installed and authenticated).
"""

from unittest.mock import MagicMock, patch

from assistant_github.client import GitHubClient, _parse_search_item


# ---------------------------------------------------------------------------
# _parse_search_item
# ---------------------------------------------------------------------------


class TestParseSearchItem:
    def test_parses_org_and_repo(self):
        item = {
            "repository_url": "https://api.github.com/repos/myorg/myrepo",
            "number": 42,
            "title": "Fix bug",
            "user": {"login": "alice"},
        }
        result = _parse_search_item(item)
        assert result["org"] == "myorg"
        assert result["repo"] == "myrepo"
        assert result["number"] == 42
        assert result["title"] == "Fix bug"
        assert result["author"] == "alice"

    def test_trailing_slash_stripped(self):
        item = {
            "repository_url": "https://api.github.com/repos/org/repo/",
            "number": 1,
            "title": "T",
            "user": {"login": "bob"},
        }
        result = _parse_search_item(item)
        assert result["org"] == "org"
        assert result["repo"] == "repo"

    def test_missing_user_defaults_empty(self):
        item = {
            "repository_url": "https://api.github.com/repos/o/r",
            "number": 1,
            "title": "T",
        }
        result = _parse_search_item(item)
        assert result["author"] == ""

    def test_invalid_url_returns_empty(self):
        item = {
            "repository_url": "",
            "number": 1,
            "title": "T",
        }
        result = _parse_search_item(item)
        assert result == {}

    def test_single_segment_url_returns_empty(self):
        item = {
            "repository_url": "x",
            "number": 1,
            "title": "T",
        }
        result = _parse_search_item(item)
        assert result == {}


# ---------------------------------------------------------------------------
# GitHubClient.get_pr — status derivation
# ---------------------------------------------------------------------------


class TestGetPrStatus:
    def test_merged_pr(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {
                "merged": True,
                "state": "closed",
                "title": "Feature",
                "user": {"login": "alice"},
            }
            result = client.get_pr("org", "repo", 1)
            assert result["status"] == "merged"

    def test_closed_pr(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {
                "merged": False,
                "state": "closed",
                "title": "Old PR",
                "user": {"login": "bob"},
            }
            result = client.get_pr("org", "repo", 2)
            assert result["status"] == "closed"

    def test_open_pr(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {
                "merged": False,
                "state": "open",
                "title": "WIP",
                "user": {"login": "carol"},
            }
            result = client.get_pr("org", "repo", 3)
            assert result["status"] == "open"

    def test_missing_fields_default(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {}
            result = client.get_pr("org", "repo", 4)
            assert result["status"] == "open"
            assert result["title"] == ""
            assert result["author"] == ""


# ---------------------------------------------------------------------------
# GitHubClient.get_pr_detail
# ---------------------------------------------------------------------------


class TestGetPrDetail:
    def test_parses_fields(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {
                "title": "Add feature",
                "body": "Description here",
                "user": {"login": "alice"},
                "additions": 50,
                "deletions": 10,
                "changed_files": 3,
            }
            result = client.get_pr_detail("org", "repo", 1)
            assert result["title"] == "Add feature"
            assert result["body"] == "Description here"
            assert result["author"] == "alice"
            assert result["additions"] == 50
            assert result["deletions"] == 10
            assert result["changed_files"] == 3

    def test_null_body_becomes_empty_string(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {"body": None, "user": {"login": "x"}}
            result = client.get_pr_detail("org", "repo", 1)
            assert result["body"] == ""


# ---------------------------------------------------------------------------
# GitHubClient.get_issue
# ---------------------------------------------------------------------------


class TestGetIssue:
    def test_parses_fields(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {
                "title": "Bug report",
                "user": {"login": "alice"},
                "state": "open",
                "labels": [{"name": "bug"}, {"name": "urgent"}],
            }
            result = client.get_issue("org", "repo", 1)
            assert result["title"] == "Bug report"
            assert result["state"] == "open"
            assert result["labels"] == ["bug", "urgent"]

    def test_empty_labels(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {
                "title": "T",
                "user": {"login": "x"},
                "state": "open",
            }
            result = client.get_issue("org", "repo", 1)
            assert result["labels"] == []


# ---------------------------------------------------------------------------
# GitHubClient.get_issue_detail
# ---------------------------------------------------------------------------


class TestGetIssueDetail:
    def test_parses_fields(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {
                "title": "Bug report",
                "body": "Steps to repro",
                "user": {"login": "alice"},
                "state": "open",
                "labels": [{"name": "bug"}],
                "comments": 5,
            }
            result = client.get_issue_detail("org", "repo", 1)
            assert result["comment_count"] == 5
            assert result["labels"] == ["bug"]

    def test_null_body(self):
        client = GitHubClient()
        with patch.object(client, "_gh_api") as mock:
            mock.return_value = {"body": None, "user": {"login": "x"}}
            result = client.get_issue_detail("org", "repo", 1)
            assert result["body"] == ""


# ---------------------------------------------------------------------------
# GitHubClient._scope_qualifiers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GitHubClient.create_issue
# ---------------------------------------------------------------------------


class TestCreateIssue:
    def test_returns_number_and_url(self):
        client = GitHubClient()
        with patch.object(client, "_run_gh") as mock:
            mock.return_value = (
                '{"number": 42, "html_url": "https://github.com/org/repo/issues/42"}'
            )
            result = client.create_issue("org", "repo", "Bug title", "Bug body")
        assert result["number"] == 42
        assert result["url"] == "https://github.com/org/repo/issues/42"

    def test_passes_title_and_body_to_gh(self):
        client = GitHubClient()
        with patch.object(client, "_run_gh") as mock:
            mock.return_value = '{"number": 1, "html_url": ""}'
            client.create_issue("myorg", "myrepo", "The title", "The body")
        cmd = mock.call_args[0][0]
        assert "repos/myorg/myrepo/issues" in cmd[2]
        assert "-f" in cmd
        assert "title=The title" in cmd
        assert "body=The body" in cmd

    def test_empty_body(self):
        client = GitHubClient()
        with patch.object(client, "_run_gh") as mock:
            mock.return_value = '{"number": 1, "html_url": ""}'
            result = client.create_issue("org", "repo", "Title")
        assert result["number"] == 1
        cmd = mock.call_args[0][0]
        assert "body=" in cmd

    def test_missing_fields_default(self):
        client = GitHubClient()
        with patch.object(client, "_run_gh") as mock:
            mock.return_value = "{}"
            result = client.create_issue("org", "repo", "T")
        assert result["number"] is None
        assert result["url"] == ""


class TestScopeQualifiers:
    def test_orgs_only(self):
        client = GitHubClient()
        integration = MagicMock()
        integration.orgs = ["myorg", "otherorg"]
        integration.repos = None
        qualifiers = client._scope_qualifiers(integration)
        assert qualifiers == ["org:myorg", "org:otherorg"]

    def test_repos_only(self):
        client = GitHubClient()
        integration = MagicMock()
        integration.orgs = None
        integration.repos = ["myorg/myrepo"]
        qualifiers = client._scope_qualifiers(integration)
        assert qualifiers == ["repo:myorg/myrepo"]

    def test_both_orgs_and_repos(self):
        client = GitHubClient()
        integration = MagicMock()
        integration.orgs = ["myorg"]
        integration.repos = ["other/repo"]
        qualifiers = client._scope_qualifiers(integration)
        assert "org:myorg" in qualifiers
        assert "repo:other/repo" in qualifiers

    def test_no_scope_returns_empty_string(self):
        client = GitHubClient()
        integration = MagicMock()
        integration.orgs = None
        integration.repos = None
        qualifiers = client._scope_qualifiers(integration)
        assert qualifiers == [""]

    def test_empty_lists_returns_empty_string(self):
        client = GitHubClient()
        integration = MagicMock()
        integration.orgs = []
        integration.repos = []
        qualifiers = client._scope_qualifiers(integration)
        assert qualifiers == [""]


# ---------------------------------------------------------------------------
# GitHubClient._search_entities — deduplication
# ---------------------------------------------------------------------------


class TestSearchDeduplication:
    def test_deduplicates_by_org_repo_number(self):
        client = GitHubClient()
        integration = MagicMock()
        integration.orgs = None
        integration.repos = None

        # _search_raw returns parsed entity dicts (not raw API items)
        parsed_results = [
            {"org": "o", "repo": "r", "number": 1, "title": "T", "author": "a"},
        ]

        with patch.object(client, "_search_raw", return_value=parsed_results):
            results = client._search_entities(
                ["query1", "query2"],
                integration,
                item_filter=None,
            )
            # Should only appear once despite being returned from two queries
            assert len(results) == 1
            assert results[0]["number"] == 1

    def test_different_entities_not_deduped(self):
        client = GitHubClient()
        integration = MagicMock()
        integration.orgs = None
        integration.repos = None

        results_q1 = [
            {"org": "o", "repo": "r", "number": 1, "title": "PR1", "author": "a"},
        ]
        results_q2 = [
            {"org": "o", "repo": "r", "number": 2, "title": "PR2", "author": "b"},
        ]

        call_count = [0]

        def fake_search_raw(query, item_filter=None):
            call_count[0] += 1
            return results_q1 if call_count[0] == 1 else results_q2

        with patch.object(client, "_search_raw", side_effect=fake_search_raw):
            results = client._search_entities(
                ["query1", "query2"],
                integration,
                item_filter=None,
            )
            assert len(results) == 2
