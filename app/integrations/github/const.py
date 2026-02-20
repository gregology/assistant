"""GitHub integration constants.

Integration-specific metadata for provenance tracking and safety validation.
"""

from app.config import ClassificationConfig

DEFAULT_CLASSIFICATIONS: dict[str, ClassificationConfig] = {
    "complexity": ClassificationConfig(
        prompt="how complex is this pull request to review? 0 = trivial, 1 = major architectural change"
    ),
    "risk": ClassificationConfig(
        prompt="how risky is this change to production systems? 0 = no risk, 1 = high risk of breaking things"
    ),
    "documentation_only": ClassificationConfig(
        prompt="is this primarily a documentation or configuration change? 0 = code change, 1 = purely documentation"
    ),
}

# Fields resolved from PR metadata (GitHub API) — not from LLM classification output.
DETERMINISTIC_SOURCES: frozenset[str] = frozenset({
    "org",
    "repo",
    "author",
    "status",
    "draft",
    "additions",
    "deletions",
    "changed_files",
})

# Actions that cannot be undone. Empty until Phase 4 defines the action model.
IRREVERSIBLE_ACTIONS: frozenset[str] = frozenset()

# Allowlist of string actions accepted by github.act. Empty until Phase 4.
# Must not grow without a reversibility tier review.
SIMPLE_ACTIONS: frozenset[str] = frozenset()
