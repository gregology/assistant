"""Tests for the web_research service handler."""

from unittest.mock import MagicMock, patch

from assistant_gemini.services.web_research import handle


def _mock_runtime(api_key="test-key", model=None):
    """Patch assistant_sdk.runtime.get_integration to return a mock config."""
    cfg = MagicMock()
    cfg.api_key = api_key
    cfg.model = model
    return patch("assistant_gemini.services.web_research.runtime.get_integration", return_value=cfg)


def _task(**payload_fields):
    """Build a full task dict wrapping the given payload fields."""
    return {"payload": payload_fields}


class TestWebResearchHandler:
    def test_basic_research(self):
        with _mock_runtime(), \
             patch("assistant_gemini.services.web_research.GeminiClient") as MockClient:
            client = MockClient.return_value
            client.grounded_search.return_value = (
                "Research results",
                [{"title": "Source", "url": "https://example.com"}],
            )

            result = handle(_task(
                integration="gemini.default",
                inputs={"prompt": "What is Assistant?"},
            ))

            assert result["text"] == "Research results"
            assert len(result["sources"]) == 1
            client.grounded_search.assert_called_once_with("What is Assistant?")

    def test_empty_prompt_returns_empty(self):
        result = handle(_task(
            integration="gemini.default",
            inputs={"prompt": ""},
        ))
        assert result == {"text": "", "sources": []}

    def test_missing_prompt_returns_empty(self):
        result = handle(_task(
            integration="gemini.default",
            inputs={},
        ))
        assert result == {"text": "", "sources": []}

    def test_with_output_schema(self):
        with _mock_runtime(), \
             patch("assistant_gemini.services.web_research.GeminiClient") as MockClient:
            client = MockClient.return_value
            client.grounded_search.return_value = (
                "Research text",
                [{"title": "S1", "url": "https://s1.com"}],
            )
            client.structured_output.return_value = {"summary": "Structured result"}

            schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
            result = handle(_task(
                integration="gemini.default",
                inputs={
                    "prompt": "Research topic",
                    "output_schema": schema,
                },
            ))

            assert result["text"] == "Research text"
            assert result["structured"] == {"summary": "Structured result"}
            client.structured_output.assert_called_once()

    def test_structured_output_failure_returns_raw(self):
        with _mock_runtime(), \
             patch("assistant_gemini.services.web_research.GeminiClient") as MockClient:
            client = MockClient.return_value
            client.grounded_search.return_value = ("Research text", [])
            client.structured_output.side_effect = Exception("API error")

            result = handle(_task(
                integration="gemini.default",
                inputs={
                    "prompt": "Research topic",
                    "output_schema": {"type": "object"},
                },
            ))

            assert result["text"] == "Research text"
            assert "structured" not in result

    def test_no_structured_pass_without_schema(self):
        with _mock_runtime(), \
             patch("assistant_gemini.services.web_research.GeminiClient") as MockClient:
            client = MockClient.return_value
            client.grounded_search.return_value = ("Text only", [])

            result = handle(_task(
                integration="gemini.default",
                inputs={"prompt": "Simple query"},
            ))

            assert result["text"] == "Text only"
            assert "structured" not in result
            client.structured_output.assert_not_called()

    def test_uses_configured_model(self):
        with _mock_runtime(model="gemini-2.5-pro"), \
             patch("assistant_gemini.services.web_research.GeminiClient") as MockClient:
            client = MockClient.return_value
            client.grounded_search.return_value = ("ok", [])

            handle(_task(
                integration="gemini.default",
                inputs={"prompt": "test"},
            ))

            MockClient.assert_called_once_with(api_key="test-key", model="gemini-2.5-pro")

    def test_default_model_when_none(self):
        with _mock_runtime(model=None), \
             patch("assistant_gemini.services.web_research.GeminiClient") as MockClient:
            client = MockClient.return_value
            client.grounded_search.return_value = ("ok", [])

            handle(_task(
                integration="gemini.default",
                inputs={"prompt": "test"},
            ))

            MockClient.assert_called_once_with(api_key="test-key", model="gemini-3-pro-preview")
