"""Tests for service result routing."""

from unittest.mock import patch

import frontmatter

from app.result_routes import route_results, _route_note


def _make_task(task_type="service.gemini.web_research", **payload_extra):
    """Build a minimal task dict for routing tests."""
    payload = {
        "type": task_type,
        "integration": "gemini.default",
        "inputs": {"prompt": "test query"},
    }
    payload.update(payload_extra)
    return {
        "id": "7_20260303T142532Z_a1b2c3d4--deadbeef--service.gemini.web_research",
        "payload": payload,
    }


class TestRouteResults:
    def test_service_task_routes_to_note_by_default(self, notes_dir):
        """Service tasks without on_result fall back to note route."""
        result = {"text": "Research output", "sources": []}
        task = _make_task()
        # No on_result in payload — should fall back to note for service tasks

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            route_results(result, task)

        # Note file should exist
        service_dir = notes_dir / "services" / "gemini" / "web_research"
        notes = list(service_dir.glob("*.md"))
        assert len(notes) == 1

    def test_explicit_on_result_note(self, notes_dir):
        """Explicit on_result with type=note saves to NoteStore."""
        result = {"text": "Explicit routing", "sources": []}
        task = _make_task(on_result=[{"type": "note"}])

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            route_results(result, task)

        service_dir = notes_dir / "services" / "gemini" / "web_research"
        notes = list(service_dir.glob("*.md"))
        assert len(notes) == 1

    def test_non_service_task_without_on_result_skipped(self, notes_dir):
        """Non-service tasks without on_result are silently skipped."""
        result = {"actions": ["archive"]}
        task = {
            "id": "7_20260303T142532Z_a1b2c3d4--abcd1234--email.inbox.act",
            "payload": {"type": "email.inbox.act"},
        }

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            route_results(result, task)

        # No notes created
        assert not (notes_dir / "services").exists()

    def test_unknown_route_type_warns(self, notes_dir):
        """Unknown route type logs a warning but does not crash."""
        result = {"text": "test"}
        task = _make_task(on_result=[{"type": "unknown_route"}])

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            route_results(result, task)

        # No crash, no notes created
        assert not (notes_dir / "services").exists()

    def test_multiple_routes(self, notes_dir):
        """Multiple note routes each produce a file."""
        result = {"text": "Multi-route test", "sources": []}
        task = _make_task(
            on_result=[
                {"type": "note"},
                {"type": "note", "path": "research/"},
            ]
        )

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            route_results(result, task)

        default_dir = notes_dir / "services" / "gemini" / "web_research"
        custom_dir = notes_dir / "research"
        assert len(list(default_dir.glob("*.md"))) == 1
        assert len(list(custom_dir.glob("*.md"))) == 1

    def test_route_failure_does_not_propagate(self, notes_dir):
        """Routing errors are logged but do not raise."""
        result = {"text": "test"}
        task = _make_task(on_result=[{"type": "note"}])

        # Force an error by making get_notes_dir raise
        with patch("app.result_routes.runtime.get_notes_dir", side_effect=RuntimeError("boom")):
            # Should not raise
            route_results(result, task)


class TestRouteNote:
    def test_note_content(self, notes_dir):
        """Note file has correct frontmatter and body content."""
        result = {
            "text": "Research body text",
            "sources": [{"title": "Source 1", "url": "https://example.com"}],
        }
        task = _make_task()
        route_config = {"type": "note"}

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            filepath = _route_note(result, task, route_config)

        assert filepath.exists()
        post = frontmatter.load(filepath)

        # Body is the text content
        assert post.content == "Research body text"

        # Frontmatter fields
        assert post["service"] == "service.gemini.web_research"
        assert post["integration"] == "gemini.default"
        assert post["inputs"] == {"prompt": "test query"}
        assert "completed_at" in post.metadata
        assert post["sources"] == [{"title": "Source 1", "url": "https://example.com"}]

    def test_note_without_text(self, notes_dir):
        """Result without text key produces note with empty body."""
        result = {"status": "ok", "count": 42}
        task = _make_task()
        route_config = {"type": "note"}

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            filepath = _route_note(result, task, route_config)

        post = frontmatter.load(filepath)
        assert post.content == ""
        assert post["status"] == "ok"
        assert post["count"] == 42

    def test_custom_path(self, notes_dir):
        """Route config path overrides default directory derivation."""
        result = {"text": "Custom path output", "sources": []}
        task = _make_task()
        route_config = {"type": "note", "path": "research/company_x"}

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            filepath = _route_note(result, task, route_config)

        assert "research" in str(filepath)
        assert "company_x" in str(filepath)
        assert filepath.exists()

    def test_default_path_derived_from_task_type(self, notes_dir):
        """Default path is services/{domain}/{service_name}/."""
        result = {"text": "test", "sources": []}
        task = _make_task(task_type="service.gemini.web_research")
        route_config = {"type": "note"}

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            filepath = _route_note(result, task, route_config)

        expected_dir = notes_dir / "services" / "gemini" / "web_research"
        assert filepath.parent == expected_dir

    def test_structured_data_in_frontmatter(self, notes_dir):
        """Structured output goes in frontmatter, not body."""
        result = {
            "text": "Research text",
            "sources": [],
            "structured": {"summary": "A structured summary"},
        }
        task = _make_task()
        route_config = {"type": "note"}

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            filepath = _route_note(result, task, route_config)

        post = frontmatter.load(filepath)
        assert post.content == "Research text"
        assert post["structured"] == {"summary": "A structured summary"}

    def test_human_log_used_when_present(self, notes_dir):
        """When payload has human_log, it is used in the log breadcrumb."""
        result = {"text": "Research output", "sources": []}
        task = _make_task(human_log="Privacy Policy update for questrade.com")
        route_config = {"type": "note"}

        with (
            patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir),
            patch("app.result_routes.log") as mock_log,
        ):
            _route_note(result, task, route_config)

        # The human log call should use the custom message
        mock_log.human.assert_called_once()
        call_args = mock_log.human.call_args[0]
        assert "Privacy Policy update for questrade.com" in call_args[1]

    def test_human_log_fallback_when_absent(self, notes_dir):
        """When payload lacks human_log, the default format is used."""
        result = {"text": "Research output", "sources": []}
        task = _make_task()  # No human_log
        route_config = {"type": "note"}

        with (
            patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir),
            patch("app.result_routes.log") as mock_log,
        ):
            _route_note(result, task, route_config)

        mock_log.human.assert_called_once()
        call_args = mock_log.human.call_args[0]
        assert "result saved" in call_args[0]

    def test_filename_contains_timestamp_and_task_id(self, notes_dir):
        """Filename includes timestamp and short task ID for uniqueness."""
        result = {"text": "test", "sources": []}
        task = _make_task()
        route_config = {"type": "note"}

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            filepath = _route_note(result, task, route_config)

        # Filename should contain the short UUID (from prefix before --)
        assert "a1b2c3d4" in filepath.name
        assert filepath.suffix == ".md"

    def test_audit_metadata_not_overwritten_by_result(self, notes_dir):
        """Service result keys matching audit fields cannot overwrite metadata."""
        result = {
            "text": "body",
            "service": "injected.service",
            "integration": "injected.integration",
            "inputs": {"injected": True},
            "completed_at": "1999-01-01T00:00:00",
        }
        task = _make_task()
        route_config = {"type": "note"}

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            filepath = _route_note(result, task, route_config)

        post = frontmatter.load(filepath)
        assert post["service"] == "service.gemini.web_research"
        assert post["integration"] == "gemini.default"
        assert post["inputs"] == {"prompt": "test query"}
        assert post["completed_at"] != "1999-01-01T00:00:00"

    def test_short_id_extraction_new_format(self, notes_dir):
        """short_id is extracted from the UUID portion, not the fingerprint or task type."""
        result = {"text": "test", "sources": []}
        task = {
            "id": "5_20260303T142532Z_beef1234--deadbeef--service.gemini.web_research",
            "payload": {
                "type": "service.gemini.web_research",
                "integration": "gemini.default",
                "inputs": {"prompt": "test"},
            },
        }
        route_config = {"type": "note"}

        with patch("app.result_routes.runtime.get_notes_dir", return_value=notes_dir):
            filepath = _route_note(result, task, route_config)

        # Should extract "beef1234" (uuid), not "deadbeef" (fingerprint)
        assert "beef1234" in filepath.name
        assert "deadbeef" not in filepath.name
