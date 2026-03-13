"""Provenance resolution for automation conditions.

Derives "rule", "llm", or "hybrid" from the namespaces used in
automation when-conditions.
"""

from __future__ import annotations

from typing import Any


def resolve_provenance(when: dict[str, Any], deterministic_sources: frozenset[str]) -> str:
    """Derive provenance from the namespaces used in automation conditions.

    Returns "rule" if all conditions reference deterministic sources,
    "llm" if all reference classification, or "hybrid" if mixed.

    deterministic_sources must be supplied by the caller from the
    integration's own const.py -- config.py holds no integration-specific
    knowledge about what is or isn't deterministic.
    """
    has_deterministic = False
    has_nondeterministic = False
    for key in when:
        namespace = key.split(".")[0]
        if namespace in deterministic_sources:
            has_deterministic = True
        else:
            has_nondeterministic = True
    if has_nondeterministic and has_deterministic:
        return "hybrid"
    if has_nondeterministic:
        return "llm"
    return "rule"
