from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestDashboard:
    def test_returns_html(self):
        response = client.get("/ui/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_contains_assistant(self):
        response = client.get("/ui/")
        assert "Assistant" in response.text


class TestConfigPage:
    def test_returns_html(self):
        response = client.get("/ui/config")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_shows_llm_profiles(self):
        response = client.get("/ui/config")
        assert "default" in response.text

    def test_shows_directories(self):
        response = client.get("/ui/config")
        assert "Directories" in response.text


class TestQueuePage:
    def test_returns_html(self):
        response = client.get("/ui/queue")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_shows_queue_dirs(self):
        response = client.get("/ui/queue")
        assert "pending" in response.text
        assert "done" in response.text


class TestLogsPage:
    def test_returns_html(self):
        response = client.get("/ui/logs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_log_detail_missing_date(self):
        response = client.get("/ui/logs/2099-01-01 Monday")
        assert response.status_code == 200
        assert "No log content found" in response.text


class TestSecretMasking:
    def test_no_secrets_in_config_page(self):
        from app.config import SECRETS_PATH

        if SECRETS_PATH.exists():
            import yaml

            raw = yaml.safe_load(SECRETS_PATH.read_text()) or {}
            secret_values = [str(v) for v in raw.values() if v is not None]
        else:
            secret_values = []

        if secret_values:
            response = client.get("/ui/config")
            for secret in secret_values:
                assert secret not in response.text, "Secret value leaked in config page"


class TestExistingEndpoints:
    def test_root_still_works(self):
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_integrations_still_works(self):
        response = client.get("/integrations")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Phase 2: Config editing POST endpoints
# ---------------------------------------------------------------------------

_MOCK_BASE = "app.ui.routes"


class TestLLMProfileEdit:
    def test_update_returns_html(self):
        with patch(f"{_MOCK_BASE}.update_llm_profile") as mock_update:
            response = client.post(
                "/ui/config/llms/default",
                data={"base_url": "http://localhost:11434", "model": "new-model"},
            )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        mock_update.assert_called_once()

    def test_validation_error_returns_422(self):
        from app.ui.yaml_rw import ConfigValidationError

        with patch(
            f"{_MOCK_BASE}.update_llm_profile",
            side_effect=ConfigValidationError("bad config"),
        ):
            response = client.post(
                "/ui/config/llms/default",
                data={"model": "new-model"},
            )
        assert response.status_code == 422
        assert "bad config" in response.text

    def test_missing_model_returns_422(self):
        response = client.post(
            "/ui/config/llms/default",
            data={"base_url": "http://localhost:11434", "model": ""},
        )
        assert response.status_code == 422
        assert "Model is required" in response.text

    def test_delete_returns_html(self):
        with patch(f"{_MOCK_BASE}.delete_llm_profile"):
            response = client.delete("/ui/config/llms/fast")
        assert response.status_code == 200

    def test_new_profile_requires_name(self):
        response = client.post(
            "/ui/config/llms/_new",
            data={"profile_name": "", "model": "some-model"},
        )
        assert response.status_code == 422
        assert "Profile name is required" in response.text


class TestDirectoriesEdit:
    def test_update_returns_html(self):
        with patch(f"{_MOCK_BASE}.update_directories"):
            response = client.post(
                "/ui/config/directories",
                data={
                    "notes": "/tmp/notes",
                    "task_queue": "data/queue",
                    "logs": "logs",
                    "custom_integrations": "",
                },
            )
        assert response.status_code == 200

    def test_missing_required_returns_422(self):
        response = client.post(
            "/ui/config/directories",
            data={"notes": "", "task_queue": "", "logs": "logs"},
        )
        assert response.status_code == 422
        assert "Task queue" in response.text


class TestScriptEdit:
    def test_create_script(self):
        with patch(f"{_MOCK_BASE}.update_script"):
            response = client.post(
                "/ui/config/scripts/_new",
                data={
                    "script_name": "test_script",
                    "shell": "echo hello",
                    "description": "A test",
                    "timeout": "60",
                    "inputs": "",
                },
            )
        assert response.status_code == 200

    def test_update_script(self):
        with patch(f"{_MOCK_BASE}.update_script"):
            response = client.post(
                "/ui/config/scripts/greet",
                data={"shell": "echo updated", "timeout": "30"},
            )
        assert response.status_code == 200

    def test_missing_shell_returns_422(self):
        response = client.post(
            "/ui/config/scripts/greet",
            data={"shell": "", "timeout": "30"},
        )
        assert response.status_code == 422
        assert "Shell command" in response.text

    def test_delete_script(self):
        with patch(f"{_MOCK_BASE}.delete_script"):
            response = client.delete("/ui/config/scripts/greet")
        assert response.status_code == 200


class TestRawYamlEditor:
    def test_valid_yaml_accepted(self):
        with patch(f"{_MOCK_BASE}.save_raw_yaml"):
            response = client.post(
                "/ui/config/yaml",
                data={"yaml_content": "llms:\n  default:\n    model: test\n"},
            )
        assert response.status_code == 200
        assert "Config saved" in response.text

    def test_invalid_yaml_rejected(self):
        from app.ui.yaml_rw import ConfigValidationError

        with patch(
            f"{_MOCK_BASE}.save_raw_yaml",
            side_effect=ConfigValidationError("Invalid YAML"),
        ):
            response = client.post(
                "/ui/config/yaml",
                data={"yaml_content": "not valid: {{"},
            )
        assert response.status_code == 422
        assert "Invalid YAML" in response.text


class TestConfigPagePhase2:
    def test_shows_edit_buttons(self):
        response = client.get("/ui/config")
        assert response.status_code == 200
        assert "Edit" in response.text

    def test_shows_raw_yaml_editor(self):
        response = client.get("/ui/config")
        assert "Raw YAML Editor" in response.text

    def test_shows_add_buttons(self):
        response = client.get("/ui/config")
        assert "+ Add" in response.text


# ---------------------------------------------------------------------------
# HTML structure tests — catch front-end integration issues
#
# These verify that forms use x-show (not template x-if) so HTMX can bind
# at page load, that interactive elements aren't trapped inside DaisyUI
# collapse-title overlays, and that form values are correct.
# ---------------------------------------------------------------------------

import re  # noqa: E402


def _get_config_html() -> str:
    return client.get("/ui/config").text


class TestFormsUseXShow:
    """Forms with hx-post/hx-delete must use x-show, not template x-if.

    Alpine's <template x-if> adds elements dynamically. HTMX only binds
    hx-* attributes at page load. If a form is inside <template x-if>,
    HTMX never processes it and submits silently fail.
    """

    def test_no_htmx_forms_inside_template_xif(self):
        html = _get_config_html()
        # Find all <template x-if> blocks and check none contain hx-post/hx-delete
        template_blocks = re.findall(
            r"<template\s+x-if[^>]*>(.*?)</template>",
            html,
            re.DOTALL,
        )
        for block in template_blocks:
            assert "hx-post" not in block, (
                "Found hx-post inside <template x-if> — HTMX won't bind. Use x-show instead."
            )
            assert "hx-delete" not in block, (
                "Found hx-delete inside <template x-if> — HTMX won't bind. Use x-show instead."
            )

    def test_edit_forms_have_hx_post(self):
        html = _get_config_html()
        # Every form on the config page should have hx-post (no bare action-less forms)
        forms = re.findall(r"<form\s[^>]*>", html)
        for form_tag in forms:
            assert "hx-post" in form_tag, (
                f"Form without hx-post found — will fall back to default GET: {form_tag[:80]}"
            )


class TestNoInteractiveElementsInCollapseTitle:
    """Buttons with hx-delete must not be inside .collapse-title.

    DaisyUI overlays an invisible checkbox on .collapse-title.
    Buttons inside it are unclickable because the checkbox intercepts clicks.
    """

    def test_no_hx_delete_in_collapse_title(self):
        html = _get_config_html()
        collapse_titles = re.findall(
            r'class="collapse-title"[^>]*>(.*?)</div>\s*<div class="collapse-content"',
            html,
            re.DOTALL,
        )
        for title_content in collapse_titles:
            assert "hx-delete" not in title_content, (
                "Found hx-delete button inside .collapse-title — "
                "DaisyUI checkbox overlay will steal clicks."
            )


class TestScheduleFormValues:
    """Schedule form fields must contain raw values, not display strings.

    The schedule display is 'every 10m' but the form value must be '10m'.
    Sending 'every 10m' as the value produces 'every: every 10m' in YAML.
    """

    def test_schedule_value_is_raw(self):
        html = _get_config_html()
        # Find schedule_value inputs and check their values don't start with "every " or "cron "
        schedule_inputs = re.findall(
            r'name="schedule_value"\s+value="([^"]*)"',
            html,
        )
        for value in schedule_inputs:
            assert not value.startswith("every "), (
                f"Schedule form value contains display prefix: '{value}'"
            )
            assert not value.startswith("cron: "), (
                f"Schedule form value contains display prefix: '{value}'"
            )


class TestPostReturnsPartial:
    """POST/DELETE responses must be HTML partials, not full pages.

    HTMX forms target specific section IDs with hx-swap="innerHTML".
    If the response is a full page (with <!DOCTYPE>, <html>, etc.),
    the entire page gets stuffed inside the targeted div, duplicating
    all sections.
    """

    def test_post_responses_are_partials(self):
        """POST to each edit endpoint should NOT return a full HTML page."""
        endpoints = [
            (
                "post",
                "/ui/config/llms/default",
                {"model": "test-model", "base_url": "http://localhost:11434"},
            ),
            (
                "post",
                "/ui/config/scripts/_new",
                {"script_name": "test_s", "shell": "echo hi", "timeout": "60"},
            ),
            (
                "post",
                "/ui/config/directories",
                {
                    "task_queue": "data/queue",
                    "logs": "logs",
                    "notes": "",
                    "custom_integrations": "",
                },
            ),
        ]
        for method, url, data in endpoints:
            with (
                patch(f"{_MOCK_BASE}.update_llm_profile"),
                patch(f"{_MOCK_BASE}.update_script"),
                patch(f"{_MOCK_BASE}.update_directories"),
            ):
                response = getattr(client, method)(url, data=data)
            assert response.status_code == 200, f"{url} returned {response.status_code}"
            assert "<!DOCTYPE" not in response.text, (
                f"{url} returned a full HTML page instead of a partial"
            )
            assert "<html" not in response.text, (
                f"{url} returned a full HTML page instead of a partial"
            )

    def test_script_save_does_not_duplicate_sections(self):
        """A scripts partial must not contain other section IDs."""
        with patch(f"{_MOCK_BASE}.update_script"):
            response = client.post(
                "/ui/config/scripts/greet",
                data={"shell": "echo hello", "timeout": "30"},
            )
        assert response.status_code == 200
        assert 'id="llm-section"' not in response.text, (
            "Scripts partial contains LLM section — section duplication bug"
        )

    def test_llm_save_does_not_duplicate_sections(self):
        """An LLM partial must not contain other section IDs."""
        with patch(f"{_MOCK_BASE}.update_llm_profile"):
            response = client.post(
                "/ui/config/llms/default",
                data={"model": "test-model", "base_url": "http://localhost:11434"},
            )
        assert response.status_code == 200
        assert 'id="scripts-section"' not in response.text, (
            "LLM partial contains scripts section — section duplication bug"
        )


class TestReloadConfigFailure:
    """POST handlers must catch non-ConfigValidationError from reload_config()."""

    def test_update_llm_reload_failure(self):
        with (
            patch(f"{_MOCK_BASE}.update_llm_profile"),
            patch(f"{_MOCK_BASE}.reload_config", side_effect=ValueError("boom")),
        ):
            response = client.post(
                "/ui/config/llms/default",
                data={"model": "test-model", "base_url": "http://localhost:11434"},
            )
        assert response.status_code == 422
        assert "saved to disk but reload failed" in response.text
        assert "boom" in response.text

    def test_remove_llm_reload_failure(self):
        with (
            patch(f"{_MOCK_BASE}.delete_llm_profile"),
            patch(f"{_MOCK_BASE}.reload_config", side_effect=ImportError("missing")),
        ):
            response = client.delete("/ui/config/llms/fast")
        assert response.status_code == 422
        assert "saved to disk but reload failed" in response.text

    def test_update_dirs_reload_failure(self):
        with (
            patch(f"{_MOCK_BASE}.update_directories"),
            patch(f"{_MOCK_BASE}.reload_config", side_effect=RuntimeError("oops")),
        ):
            response = client.post(
                "/ui/config/directories",
                data={
                    "notes": "",
                    "task_queue": "data/queue",
                    "logs": "logs",
                    "custom_integrations": "",
                },
            )
        assert response.status_code == 422
        assert "saved to disk but reload failed" in response.text

    def test_update_script_reload_failure(self):
        with (
            patch(f"{_MOCK_BASE}.update_script"),
            patch(f"{_MOCK_BASE}.reload_config", side_effect=ValueError("bad")),
        ):
            response = client.post(
                "/ui/config/scripts/greet",
                data={"shell": "echo hello", "timeout": "30"},
            )
        assert response.status_code == 422
        assert "saved to disk but reload failed" in response.text

    def test_remove_script_reload_failure(self):
        with (
            patch(f"{_MOCK_BASE}.delete_script"),
            patch(f"{_MOCK_BASE}.reload_config", side_effect=ValueError("bad")),
        ):
            response = client.delete("/ui/config/scripts/greet")
        assert response.status_code == 422
        assert "saved to disk but reload failed" in response.text

    def test_save_raw_reload_failure(self):
        with (
            patch(f"{_MOCK_BASE}.save_raw_yaml"),
            patch(f"{_MOCK_BASE}.reload_config", side_effect=ValueError("bad")),
        ):
            response = client.post(
                "/ui/config/yaml",
                data={"yaml_content": "llms:\n  default:\n    model: test\n"},
            )
        assert response.status_code == 422
        assert "saved to disk but reload failed" in response.text

    def test_update_integration_reload_failure(self):
        with (
            patch(f"{_MOCK_BASE}.update_integration_settings"),
            patch(f"{_MOCK_BASE}.reload_config", side_effect=ValueError("bad")),
        ):
            response = client.post(
                "/ui/config/integrations/0/settings",
                data={"schedule_type": "none", "schedule_value": ""},
            )
        assert response.status_code == 422
        assert "saved to disk but reload failed" in response.text


class TestPresenterViewModels:
    """IntegrationView must carry raw schedule fields for forms."""

    def test_integration_view_has_schedule_fields(self):
        from app.ui.presenters import config_context

        ctx = config_context()
        for integration in ctx["integrations"]:
            assert hasattr(integration, "schedule_type")
            assert hasattr(integration, "schedule_value")
            assert integration.schedule_type in ("every", "cron", "none")
            if integration.schedule_type != "none":
                # Raw value must not contain the type prefix
                assert not integration.schedule_value.startswith("every ")
                assert not integration.schedule_value.startswith("cron ")
