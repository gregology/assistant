"""Tests for assistant_sdk.provenance — provenance resolution from condition keys."""

from assistant_sdk.provenance import resolve_provenance

DETERMINISTIC = frozenset({"domain", "org", "repo", "author", "authentication"})


class TestResolveProvenance:
    def test_all_deterministic_is_rule(self):
        when = {"domain": "example.com", "author": "alice"}
        assert resolve_provenance(when, DETERMINISTIC) == "rule"

    def test_all_classification_is_llm(self):
        when = {"classification.score": "> 0.8", "classification.flag": True}
        assert resolve_provenance(when, DETERMINISTIC) == "llm"

    def test_mixed_is_hybrid(self):
        when = {"domain": "example.com", "classification.score": "> 0.8"}
        assert resolve_provenance(when, DETERMINISTIC) == "hybrid"

    def test_nested_deterministic_is_rule(self):
        when = {"authentication.dkim_pass": True}
        assert resolve_provenance(when, DETERMINISTIC) == "rule"

    def test_empty_when_is_rule(self):
        assert resolve_provenance({}, DETERMINISTIC) == "rule"

    def test_unknown_namespace_is_llm(self):
        when = {"something_unknown": True}
        assert resolve_provenance(when, DETERMINISTIC) == "llm"
