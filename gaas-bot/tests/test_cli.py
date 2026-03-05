"""Tests for the gaas-bot CLI structure."""

from click.testing import CliRunner

from gaas_bot.cli import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "gaas-bot" in result.output


def test_resolve_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["resolve", "--help"])
    assert result.exit_code == 0
    assert "--issue" in result.output
    assert "--owner" in result.output
    assert "--repo" in result.output


def test_audit_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "--help"])
    assert result.exit_code == 0
    assert "docs" in result.output
    assert "refactor" in result.output
    assert "tests" in result.output


def test_audit_docs_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "docs", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output
    assert "--owner" in result.output
    assert "--repo" in result.output


def test_audit_refactor_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "refactor", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output


def test_audit_tests_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "tests", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output
