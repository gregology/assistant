"""Email inbox platform constants.

Platform-specific metadata for provenance tracking and safety validation.
"""

from assistant_sdk.models import ClassificationConfig

DEFAULT_CLASSIFICATIONS: dict[str, ClassificationConfig] = {
    "human": ClassificationConfig(prompt="is this a personal email written by a human?"),
    "user_agreement_update": ClassificationConfig(
        prompt="is this email about a user agreement update?", type="boolean"
    ),
    "requires_response": ClassificationConfig(
        prompt="does this email require a response?", type="boolean"
    ),
    "priority": ClassificationConfig(
        prompt="what is the priority of this email?",
        type="enum",
        values=["low", "medium", "high", "critical"],
    ),
}

# Fields that are resolved from the email object itself (IMAP data, headers,
# authentication results) — not from LLM classification output.
DETERMINISTIC_SOURCES: frozenset[str] = frozenset({
    "authentication",
    "calendar",
    "domain",
    "from_address",
    "has_attachments",
    "is_answered",
    "is_calendar_event",
    "is_forward",
    "is_noreply",
    "is_read",
    "is_reply",
    "is_starred",
    "is_unsubscribable",
    "root_domain",
})

# Actions that cannot be undone. Automations that would trigger these from
# non-deterministic (LLM) provenance are blocked at config load time unless
# the user explicitly tags them with !yolo.
IRREVERSIBLE_ACTIONS: frozenset[str] = frozenset({"unsubscribe"})

# Allowlist of string actions accepted by email.inbox.act. Any string action not
# in this set is skipped with a warning and never executed.
# Must not grow without a reversibility tier review.
SIMPLE_ACTIONS: frozenset[str] = frozenset({"archive", "spam", "trash", "unsubscribe"})
