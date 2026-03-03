"""Tests for config-driven queue policies: dedup + rate limiting."""

from unittest.mock import patch

import pytest

from app import queue
from app.config import QueuePolicyConfig, RateLimitConfig, TaskPolicyConfig
from app.queue_policy import _parse_duration_seconds, policy_enqueue, resolve_policy


# ---------------------------------------------------------------------------
# _parse_duration_seconds
# ---------------------------------------------------------------------------


class TestParseDurationSeconds:
    def test_minutes(self):
        assert _parse_duration_seconds("30m") == 1800

    def test_hours(self):
        assert _parse_duration_seconds("1h") == 3600

    def test_days(self):
        assert _parse_duration_seconds("1d") == 86400

    def test_whitespace(self):
        assert _parse_duration_seconds("  2h  ") == 7200

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            _parse_duration_seconds("abc")

    def test_no_unit_raises(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            _parse_duration_seconds("30")


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_deterministic(self):
        payload = {"type": "email.inbox.check", "integration": "email.personal"}
        assert queue.fingerprint(payload) == queue.fingerprint(payload)

    def test_key_order_irrelevant(self):
        a = {"type": "test", "integration": "foo"}
        b = {"integration": "foo", "type": "test"}
        assert queue.fingerprint(a) == queue.fingerprint(b)

    def test_different_payloads_differ(self):
        a = {"type": "test", "value": 1}
        b = {"type": "test", "value": 2}
        assert queue.fingerprint(a) != queue.fingerprint(b)

    def test_length_is_8(self):
        assert len(queue.fingerprint({"type": "test"})) == 8


# ---------------------------------------------------------------------------
# parse_filename
# ---------------------------------------------------------------------------


class TestParseFilename:
    def test_valid_format(self):
        result = queue.parse_filename("5_20260303T142532Z_a1b2c3d4--deadbeef--email.inbox.check.yaml")
        assert result == {
            "priority": "5",
            "timestamp": "20260303T142532Z",
            "uuid": "a1b2c3d4",
            "fingerprint": "deadbeef",
            "task_type": "email.inbox.check",
        }

    def test_underscore_in_task_type(self):
        result = queue.parse_filename("5_20260303T142532Z_a1b2c3d4--deadbeef--github.pull_requests.classify.yaml")
        assert result is not None
        assert result["task_type"] == "github.pull_requests.classify"

    def test_invalid_format_no_dashes(self):
        assert queue.parse_filename("5_20260303T142532Z_a1b2c3d4.yaml") is None

    def test_invalid_format_wrong_prefix(self):
        assert queue.parse_filename("bad--deadbeef--test.yaml") is None

    def test_legacy_format(self):
        """Old format without -- separators returns None."""
        assert queue.parse_filename("5_20260303T142532Z_a1b2c3d4.yaml") is None


# ---------------------------------------------------------------------------
# has_pending_duplicate
# ---------------------------------------------------------------------------


class TestHasPendingDuplicate:
    def test_no_duplicate(self, queue_dir):
        assert queue.has_pending_duplicate("deadbeef", "email.inbox.check") is False

    def test_detects_duplicate(self, queue_dir):
        payload = {"type": "email.inbox.check", "integration": "email.personal"}
        queue.enqueue(payload)
        fp = queue.fingerprint(payload)
        assert queue.has_pending_duplicate(fp, "email.inbox.check") is True

    def test_ignores_active(self, queue_dir):
        """Duplicate check only looks at pending, not active."""
        payload = {"type": "email.inbox.check", "integration": "email.personal"}
        queue.enqueue(payload)
        task = queue.dequeue()  # moves to active
        fp = queue.fingerprint(payload)
        assert queue.has_pending_duplicate(fp, "email.inbox.check") is False


# ---------------------------------------------------------------------------
# count_recent
# ---------------------------------------------------------------------------


class TestCountRecent:
    def test_counts_across_dirs(self, queue_dir):
        payload = {"type": "email.inbox.check", "integration": "email.personal"}
        queue.enqueue(payload)
        queue.enqueue(payload)
        # One in pending, move one to done
        task = queue.dequeue()
        queue.complete(task["id"])

        count = queue.count_recent("email.inbox.check", 3600)
        assert count == 2

    def test_zero_when_empty(self, queue_dir):
        assert queue.count_recent("email.inbox.check", 3600) == 0

    def test_ignores_different_types(self, queue_dir):
        queue.enqueue({"type": "email.inbox.check"})
        queue.enqueue({"type": "github.pull_requests.classify"})

        assert queue.count_recent("email.inbox.check", 3600) == 1
        assert queue.count_recent("github.pull_requests.classify", 3600) == 1


# ---------------------------------------------------------------------------
# resolve_policy
# ---------------------------------------------------------------------------


class TestResolvePolicy:
    def test_defaults(self):
        with patch("app.queue_policy.config") as mock_config:
            mock_config.queue_policies = QueuePolicyConfig()
            policy = resolve_policy("email.inbox.check")
            assert policy.deduplicate_pending is True
            assert policy.rate_limit is None

    def test_override(self):
        with patch("app.queue_policy.config") as mock_config:
            mock_config.queue_policies = QueuePolicyConfig(
                overrides={
                    "service.gemini.web_research": TaskPolicyConfig(
                        rate_limit=RateLimitConfig(max=10, per="1h"),
                    )
                }
            )
            policy = resolve_policy("service.gemini.web_research")
            assert policy.deduplicate_pending is True  # inherited from default
            assert policy.rate_limit is not None
            assert policy.rate_limit.max == 10

    def test_override_inherits_non_default_defaults(self):
        """Override without deduplicate_pending inherits from defaults, even when
        the configured default differs from the Pydantic field default."""
        with patch("app.queue_policy.config") as mock_config:
            mock_config.queue_policies = QueuePolicyConfig(
                defaults=TaskPolicyConfig(deduplicate_pending=False),
                overrides={
                    "service.gemini.web_research": TaskPolicyConfig(
                        rate_limit=RateLimitConfig(max=10, per="1h"),
                    )
                }
            )
            policy = resolve_policy("service.gemini.web_research")
            assert policy.deduplicate_pending is False  # inherited from configured default
            assert policy.rate_limit is not None
            assert policy.rate_limit.max == 10

    def test_unmatched_type_gets_defaults(self):
        with patch("app.queue_policy.config") as mock_config:
            mock_config.queue_policies = QueuePolicyConfig(
                overrides={
                    "service.gemini.web_research": TaskPolicyConfig(
                        rate_limit=RateLimitConfig(max=10, per="1h"),
                    )
                }
            )
            policy = resolve_policy("email.inbox.check")
            assert policy.deduplicate_pending is True
            assert policy.rate_limit is None


# ---------------------------------------------------------------------------
# policy_enqueue (integration tests)
# ---------------------------------------------------------------------------


class TestPolicyEnqueue:
    def test_enqueues_normally(self, queue_dir):
        """Without any policy config, tasks are enqueued normally."""
        task_id = policy_enqueue({"type": "email.inbox.check"})
        assert task_id is not None
        assert queue.dequeue() is not None

    def test_skips_duplicate(self, queue_dir):
        """Second enqueue of identical payload is skipped."""
        payload = {"type": "email.inbox.check", "integration": "email.personal"}
        first = policy_enqueue(payload)
        second = policy_enqueue(payload)
        assert first is not None
        assert second is None

    def test_allows_different_payloads(self, queue_dir):
        """Different payloads with same type are not deduped."""
        first = policy_enqueue({"type": "email.inbox.collect", "uid": "1"})
        second = policy_enqueue({"type": "email.inbox.collect", "uid": "2"})
        assert first is not None
        assert second is not None

    def test_rate_limit_blocks(self, queue_dir):
        """Rate limit prevents enqueue when count exceeds max."""
        with patch("app.queue_policy.config") as mock_config:
            mock_config.queue_policies = QueuePolicyConfig(
                defaults=TaskPolicyConfig(deduplicate_pending=False),
                overrides={
                    "service.gemini.web_research": TaskPolicyConfig(
                        deduplicate_pending=False,
                        rate_limit=RateLimitConfig(max=2, per="1h"),
                    )
                },
            )
            p1 = policy_enqueue({"type": "service.gemini.web_research", "prompt": "a"})
            p2 = policy_enqueue({"type": "service.gemini.web_research", "prompt": "b"})
            p3 = policy_enqueue({"type": "service.gemini.web_research", "prompt": "c"})
            assert p1 is not None
            assert p2 is not None
            assert p3 is None  # blocked by rate limit

    def test_no_policy_passthrough(self, queue_dir):
        """With dedup disabled and no rate limit, all enqueues succeed."""
        with patch("app.queue_policy.config") as mock_config:
            mock_config.queue_policies = QueuePolicyConfig(
                defaults=TaskPolicyConfig(deduplicate_pending=False),
            )
            payload = {"type": "email.inbox.check"}
            first = policy_enqueue(payload)
            second = policy_enqueue(payload)
            assert first is not None
            assert second is not None

    def test_dedup_cleared_after_dequeue(self, queue_dir):
        """After a task is dequeued (moved to active), same payload can be enqueued again."""
        payload = {"type": "email.inbox.check", "integration": "email.personal"}
        first = policy_enqueue(payload)
        assert first is not None

        # Move to active
        queue.dequeue()

        # Now pending is clear, should allow re-enqueue
        second = policy_enqueue(payload)
        assert second is not None
