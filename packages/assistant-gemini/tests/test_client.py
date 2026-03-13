"""Tests for GeminiClient — mocks google.genai.Client."""

from unittest.mock import MagicMock, patch

from assistant_gemini.client import GeminiClient


def _mock_genai_client():
    """Create a mock google.genai.Client with generate_content."""
    mock = MagicMock()
    return mock


class TestGroundedSearch:
    def test_returns_text_and_sources(self):
        with patch("assistant_gemini.client.genai.Client") as MockClient:
            client_instance = MockClient.return_value

            # Build mock response with grounding metadata
            web_chunk = MagicMock()
            web_chunk.web.title = "Example Article"
            web_chunk.web.uri = "https://example.com/article"

            candidate = MagicMock()
            candidate.grounding_metadata.grounding_chunks = [web_chunk]

            response = MagicMock()
            response.text = "Research findings about the topic."
            response.candidates = [candidate]

            client_instance.models.generate_content.return_value = response

            gemini = GeminiClient(api_key="test-key")
            text, sources = gemini.grounded_search("test query")

            assert text == "Research findings about the topic."
            assert len(sources) == 1
            assert sources[0]["title"] == "Example Article"
            assert sources[0]["url"] == "https://example.com/article"

    def test_empty_response(self):
        with patch("assistant_gemini.client.genai.Client") as MockClient:
            client_instance = MockClient.return_value

            response = MagicMock()
            response.text = ""
            response.candidates = []

            client_instance.models.generate_content.return_value = response

            gemini = GeminiClient(api_key="test-key")
            text, sources = gemini.grounded_search("test query")

            assert text == ""
            assert sources == []

    def test_multiple_sources(self):
        with patch("assistant_gemini.client.genai.Client") as MockClient:
            client_instance = MockClient.return_value

            chunks = []
            for i in range(3):
                chunk = MagicMock()
                chunk.web.title = f"Source {i}"
                chunk.web.uri = f"https://example.com/{i}"
                chunks.append(chunk)

            candidate = MagicMock()
            candidate.grounding_metadata.grounding_chunks = chunks

            response = MagicMock()
            response.text = "Multi-source research."
            response.candidates = [candidate]

            client_instance.models.generate_content.return_value = response

            gemini = GeminiClient(api_key="test-key")
            _text, sources = gemini.grounded_search("test query")

            assert len(sources) == 3
            assert sources[2]["title"] == "Source 2"

    def test_no_grounding_metadata(self):
        with patch("assistant_gemini.client.genai.Client") as MockClient:
            client_instance = MockClient.return_value

            candidate = MagicMock(spec=[])  # no grounding_metadata attribute
            response = MagicMock()
            response.text = "Response without grounding."
            response.candidates = [candidate]

            client_instance.models.generate_content.return_value = response

            gemini = GeminiClient(api_key="test-key")
            text, sources = gemini.grounded_search("test query")

            assert text == "Response without grounding."
            assert sources == []

    def test_uses_configured_model(self):
        with patch("assistant_gemini.client.genai.Client") as MockClient:
            client_instance = MockClient.return_value

            response = MagicMock()
            response.text = "ok"
            response.candidates = []
            client_instance.models.generate_content.return_value = response

            gemini = GeminiClient(api_key="test-key", model="gemini-2.5-pro")
            gemini.grounded_search("test")

            call_args = client_instance.models.generate_content.call_args
            assert call_args.kwargs["model"] == "gemini-2.5-pro"


class TestStructuredOutput:
    def test_returns_parsed_json(self):
        with patch("assistant_gemini.client.genai.Client") as MockClient:
            client_instance = MockClient.return_value

            response = MagicMock()
            response.text = '{"summary": "Test result", "confidence": 0.9}'

            client_instance.models.generate_content.return_value = response

            gemini = GeminiClient(api_key="test-key")
            result = gemini.structured_output(
                "Summarize this",
                {"type": "object", "properties": {"summary": {"type": "string"}}},
            )

            assert result["summary"] == "Test result"
            assert result["confidence"] == 0.9

    def test_passes_schema_to_config(self):
        with patch("assistant_gemini.client.genai.Client") as MockClient:
            client_instance = MockClient.return_value

            response = MagicMock()
            response.text = '{"answer": true}'
            client_instance.models.generate_content.return_value = response

            schema = {"type": "object", "properties": {"answer": {"type": "boolean"}}}
            gemini = GeminiClient(api_key="test-key")
            gemini.structured_output("test", schema)

            call_args = client_instance.models.generate_content.call_args
            config = call_args.kwargs["config"]
            assert config.response_mime_type == "application/json"
            assert config.response_schema == schema
