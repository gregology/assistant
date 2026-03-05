"""Structured output models for the resolve pipeline."""

from enum import Enum

from pydantic import BaseModel


class TriageDecision(str, Enum):
    ASK = "ask"
    PROPOSE = "propose"
    ACTION_PROPOSED = "action_proposed"
    ACTION_DIRECT = "action_direct"


class TriageResult(BaseModel):
    decision: TriageDecision
    reasoning: str
    detail: str


class CommentResult(BaseModel):
    comment: str


class EvalDecision(str, Enum):
    PASS = "pass"
    NEEDS_CONTEXT = "needs_context"


class EvalResult(BaseModel):
    decision: EvalDecision
    feedback: str


class PRResult(BaseModel):
    pr_body: str
    commit_message: str
    branch_name: str
