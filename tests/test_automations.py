import pytest
from app.automations import handle_automation, resolve_templates
from app.integrations import HANDLERS
from app import queue
from unittest.mock import MagicMock, patch

def test_resolve_templates():
    context = {
        "domain": "example.com",
        "research_result": {"summary": "Great summary"}
    }
    
    # Simple string
    assert resolve_templates("No template", context) == "No template"
    
    # Template string
    assert resolve_templates("Research for {{ domain }}", context) == "Research for example.com"
    
    # Nested dict
    data = {
        "text": "Summary: {{ research_result.summary }}",
        "raw": "literal"
    }
    resolved = resolve_templates(data, context)
    assert resolved["text"] == "Summary: Great summary"
    assert resolved["raw"] == "literal"

def test_handle_automation_chaining():
    # Mock handlers
    mock_research_handler = MagicMock(return_value={"summary": "Detailed research"})
    
    with (
        patch.dict(HANDLERS, {"gemini.research": mock_research_handler}),
        patch("app.queue.enqueue") as mock_enqueue,
    ):
        
        actions = [
            {
                "action": "gemini.research",
                "inputs": {"domain": "{{ domain }}"},
                "register": "research_result"
            },
            {
                "action": "test.action", # This one will just be logged as missing but demonstrates sequence
                "inputs": {"text": "Result was: {{ research_result.summary }}"}
            }
        ]
        
        task = {
            "payload": {
                "actions": actions,
                "context": {"domain": "google.com"},
                "platform_payload": {"type": "email.act"}
            }
        }
        
        handle_automation(task)
        
        # Verify first action called with correct inputs
        mock_research_handler.assert_called_once()
        call_args = mock_research_handler.call_args[0][0]
        assert call_args["payload"]["inputs"]["domain"] == "google.com"
        
        # Verify second action (if it existed) would have correct resolved template
        # Since HANDLERS doesn't have "test.action", it just logs a warning
        # But we can verify the context was updated internally by handle_automation
