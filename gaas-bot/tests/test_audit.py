"""Tests for audit issue filing logic."""

from click.testing import CliRunner
from unittest.mock import patch, MagicMock

from gaas_bot.commands.audit import file_issues
from gaas_bot.models.audit import AuditFinding, AuditReport


def test_file_issues_dry_run_prints_findings():
    report = AuditReport(findings=[
        AuditFinding(title="Stale docs", body="Details here", labels=["Docs"]),
        AuditFinding(title="Missing test", body="More details", labels=["Tests", "Safety"]),
    ])
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            _make_dry_run_command(report),
            [],
        )
    assert "Stale docs" in result.output
    assert "Missing test" in result.output
    assert "Tests, Safety" in result.output
    assert "dry run: 2 finding(s)" in result.output


def test_file_issues_empty_report():
    report = AuditReport(findings=[])
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            _make_dry_run_command(report),
            [],
        )
    assert "no findings" in result.output


def test_file_issues_creates_github_issues():
    report = AuditReport(findings=[
        AuditFinding(title="Issue 1", body="Body 1", labels=["Docs"]),
        AuditFinding(title="Issue 2", body="Body 2", labels=["Tests"]),
    ])

    mock_gh = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.gh = mock_gh

    with patch("gaas_bot.commands.audit.build_github_context", return_value=mock_ctx):
        with patch("gaas_bot.commands.audit.create_issue", return_value="https://github.com/test/1") as mock_create:
            file_issues(report, owner="testowner", repo="testrepo", dry_run=False)

    assert mock_create.call_count == 2
    mock_create.assert_any_call(
        mock_gh, "testowner", "testrepo",
        title="Issue 1", body="Body 1", labels=["Docs"],
    )
    mock_create.assert_any_call(
        mock_gh, "testowner", "testrepo",
        title="Issue 2", body="Body 2", labels=["Tests"],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dry_run_command(report):
    """Create a Click command that calls file_issues for testing output capture."""
    import click

    @click.command()
    def cmd():
        file_issues(report, owner="test", repo="test", dry_run=True)

    return cmd
