"""Structured output models for audit commands."""

from pydantic import BaseModel


class AuditFinding(BaseModel):
    title: str
    body: str
    labels: list[str]


class AuditReport(BaseModel):
    findings: list[AuditFinding]
