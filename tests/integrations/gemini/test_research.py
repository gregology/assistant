import pytest
from app.integrations.gemini.actions.research import handle
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_config():
    with patch("app.integrations.gemini.actions.research.config") as mock:
        # Mock the integration list
        gemini_integration = MagicMock()
        gemini_integration.type = "gemini"
        gemini_integration.api_key = "test-key"
        gemini_integration.model = "test-model"
        gemini_integration.name = "test-gemini"
        mock.integrations = [gemini_integration]
        yield mock

def test_research_execution(mock_config):
    # Mock the GeminiClient to avoid real API calls
    with patch("app.integrations.gemini.actions.research.GeminiClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.research.return_value = {
            "summary": "Mocked summary",
            "relevant_changes": []
        }
        
        task = {
            "payload": {
                "inputs": {
                    "domain": "google.com",
                    "focus": "privacy policy changes"
                }
            }
        }
        
        result = handle(task)
        assert result["summary"] == "Mocked summary"
        mock_client.research.assert_called_once_with("google.com", "privacy policy changes")

def test_research_missing_domain(mock_config):
    task = {
        "payload": {
            "inputs": {}
        }
    }
    with pytest.raises(ValueError, match="domain' input is required"):
        handle(task)

def test_research_missing_integration():
    with patch("app.integrations.gemini.actions.research.config") as mock:
        mock.integrations = [] # No gemini integration
        task = {
            "payload": {
                "inputs": {"domain": "test.com"}
            }
        }
        with pytest.raises(ValueError, match="No 'gemini' integration found"):
            handle(task)
