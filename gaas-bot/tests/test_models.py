"""Tests for structured output models."""

from gaas_bot.models.audit import AuditFinding, AuditReport
from gaas_bot.models.resolve import (
    CommentResult,
    EvalDecision,
    EvalResult,
    PRResult,
    TriageDecision,
    TriageResult,
)


# ---------------------------------------------------------------------------
# Resolve models
# ---------------------------------------------------------------------------

def test_triage_result_roundtrip():
    data = {"decision": "ask", "reasoning": "unclear", "detail": "need more info"}
    result = TriageResult.model_validate(data)
    assert result.decision == TriageDecision.ASK
    assert result.model_dump()["decision"] == "ask"


def test_eval_result_roundtrip():
    data = {"decision": "pass", "feedback": "looks good"}
    result = EvalResult.model_validate(data)
    assert result.decision == EvalDecision.PASS


def test_pr_result_roundtrip():
    data = {"pr_body": "body", "commit_message": "fix thing", "branch_name": "fix-thing"}
    result = PRResult.model_validate(data)
    assert result.branch_name == "fix-thing"


def test_comment_result():
    data = {"comment": "some markdown"}
    result = CommentResult.model_validate(data)
    assert result.comment == "some markdown"


# ---------------------------------------------------------------------------
# Audit models
# ---------------------------------------------------------------------------

def test_audit_finding_roundtrip():
    data = {
        "title": "Config docs list wrong default port",
        "body": "**File**: `CLAUDE.md`\n\n**What the docs say**: port 8080\n**What the code does**: port 6767",
        "labels": ["Docs"],
    }
    finding = AuditFinding.model_validate(data)
    assert finding.title == "Config docs list wrong default port"
    assert finding.labels == ["Docs"]


def test_audit_report_empty():
    report = AuditReport.model_validate({"findings": []})
    assert report.findings == []


def test_audit_report_multiple_findings():
    data = {
        "findings": [
            {"title": "Finding 1", "body": "Body 1", "labels": ["Docs"]},
            {"title": "Finding 2", "body": "Body 2", "labels": ["Tests", "Safety"]},
        ],
    }
    report = AuditReport.model_validate(data)
    assert len(report.findings) == 2
    assert report.findings[1].labels == ["Tests", "Safety"]


def test_audit_finding_preserves_markdown_body():
    body = "## Summary\n\nSome **bold** text.\n\n```python\nprint('hello')\n```"
    finding = AuditFinding.model_validate({"title": "t", "body": body, "labels": ["Tech debt"]})
    assert "```python" in finding.body
