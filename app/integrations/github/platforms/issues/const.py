"""GitHub issues platform constants.

Platform-specific metadata for provenance tracking and safety validation.
"""

from app.config import ClassificationConfig

DEFAULT_CLASSIFICATIONS: dict[str, ClassificationConfig] = {
    "urgency": ClassificationConfig(
        prompt="How urgently does this issue need attention? 0 = no urgency, 1 = critical.",
    ),
    "actionable": ClassificationConfig(
        prompt="Can you take a concrete next step on this issue right now?",
        type="boolean",
    ),
}

# Fields resolved from issue metadata — not from LLM classification output.
DETERMINISTIC_SOURCES: frozenset[str] = frozenset({
    "org",
    "repo",
    "author",
    "state",
    "labels",
    "comment_count",
})

# Actions that cannot be undone. Empty — no write actions yet.
IRREVERSIBLE_ACTIONS: frozenset[str] = frozenset()

# Allowlist of string actions accepted by act handler. Empty — no write actions yet.
SIMPLE_ACTIONS: frozenset[str] = frozenset()
