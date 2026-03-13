"""GitHub pull requests platform constants.

Platform-specific metadata for provenance tracking and safety validation.
"""

from assistant_sdk.models import ClassificationConfig

DEFAULT_CLASSIFICATIONS: dict[str, ClassificationConfig] = {
    "complexity": ClassificationConfig(
        prompt=(
            "How complex is this pull request to review?"
            " 0 = trivial typo fix, 1 = major architectural change."
        ),
    ),
    "risk": ClassificationConfig(
        prompt=(
            "How risky is this change to production systems?"
            " 0 = no risk, 1 = high risk of breaking things."
        ),
    ),
    "documentation_only": ClassificationConfig(
        prompt=(
            "Is this primarily a documentation or configuration change?"
            " 0 = code change, 1 = purely documentation."
        ),
        type="boolean",
    ),
}

# Fields resolved from PR metadata — not from LLM classification output.
# Each entry is the top-level key namespace used in automation `when` conditions.
DETERMINISTIC_SOURCES: frozenset[str] = frozenset({
    "org",
    "repo",
    "number",
    "author",
    "title",
    "status",
    "additions",
    "deletions",
    "changed_files",
})

# Actions that cannot be undone. Empty until write actions are added.
# Automations that would trigger these from non-deterministic (LLM) provenance
# are blocked at config load time unless the user tags them with !yolo.
IRREVERSIBLE_ACTIONS: frozenset[str] = frozenset()

# Allowlist of string actions accepted by act handler. Empty until write actions added.
# Must not grow without a reversibility tier review.
SIMPLE_ACTIONS: frozenset[str] = frozenset()
