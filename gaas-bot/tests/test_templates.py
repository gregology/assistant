"""Tests for template rendering."""

from gaas_bot.core.templates import TEMPLATES_DIR, render


def test_templates_dir_exists():
    assert TEMPLATES_DIR.is_dir()


def test_render_resolve_triage():
    ctx = {
        "owner": "testowner",
        "repo": "testrepo",
        "issue_number": 1,
        "issue_title": "Test issue",
        "issue_body": "Test body",
        "issue_author": "testuser",
        "issue_state": "open",
        "issue_labels": ["bug"],
        "comments": [],
    }
    result = render("resolve_triage.md.j2", ctx)
    assert "testowner/testrepo" in result
    assert "Test issue" in result
    assert "bug" in result


def test_render_resolve_triage_with_comments():
    ctx = {
        "owner": "o",
        "repo": "r",
        "issue_number": 1,
        "issue_title": "t",
        "issue_body": "",
        "issue_author": "a",
        "issue_state": "open",
        "issue_labels": [],
        "comments": [
            {"author": "user1", "created_at": "2025-01-01 10:00", "body": "comment text"},
        ],
    }
    result = render("resolve_triage.md.j2", ctx)
    assert "comment text" in result
    assert "user1" in result


def test_audit_docs_includes_labels():
    result = render("audit_docs.md.j2", {"max_findings": 3})
    assert "Available labels" in result
    assert "`Docs`" in result
    assert "`Safety`" in result


def test_audit_docs_respects_limit():
    result = render("audit_docs.md.j2", {"max_findings": 7})
    assert "Limit to 7 findings" in result


def test_audit_refactor_includes_labels():
    result = render("audit_refactor.md.j2", {"tool_output": "no issues", "max_findings": 3})
    assert "Available labels" in result
    assert "`Tech debt`" in result


def test_audit_refactor_respects_limit():
    result = render("audit_refactor.md.j2", {"tool_output": "", "max_findings": 5})
    assert "at most 5" in result


def test_audit_tests_includes_labels():
    result = render("audit_tests.md.j2", {"max_findings": 3})
    assert "Available labels" in result
    assert "`Tests`" in result


def test_audit_tests_respects_limit():
    result = render("audit_tests.md.j2", {"max_findings": 10})
    assert "Limit to 10 findings" in result


def test_labels_template_standalone():
    result = render("_labels.md.j2", {})
    assert "`Docs`" in result
    assert "`Tests`" in result
    assert "`Inconsistent patterns`" in result
    assert "`Safety`" in result
    assert "`Tech debt`" in result
    assert "`Stale decision`" in result
    assert "`Configuration`" in result
    assert "`Type safety`" in result
    assert "`Error handling`" in result
    assert "`Dead code`" in result
