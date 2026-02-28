import logging
from typing import Any
from app.config import config
import app.human_log  # noqa: F401 — registers log.human()
from ..client import GeminiClient

log = logging.getLogger(__name__)
human_log = logging.getLogger("gemini.research")

def handle(task: dict[str, Any]) -> dict[str, Any]:
    """
    Execute the gemini.research action.
    
    Inputs:
        prompt (str): The research query.
        schema (dict, optional): JSON schema for structured output.
    """
    payload = task.get("payload", {})
    inputs = payload.get("inputs", {})
    prompt = inputs.get("prompt")
    schema = inputs.get("schema") # No more hardcoded defaults
    
    if not prompt:
        raise ValueError("gemini.research: 'prompt' input is required")

    gemini_config = next((i for i in config.integrations if i.type == "gemini"), None)
    if not gemini_config:
        raise ValueError("gemini.research: No 'gemini' integration found in configuration")

    api_key = getattr(gemini_config, "api_key", None)
    model = getattr(gemini_config, "model", "gemini-2.0-pro-exp-02-05")
    
    if not api_key:
        raise ValueError(f"gemini.research: API key not found for integration '{gemini_config.name}'")

    log.info("gemini.research: executing prompt using model=%s (structured=%s)", model, schema is not None)
    
    client = GeminiClient(api_key=api_key, model_name=model)
    result = client.generate(prompt=prompt, response_schema=schema, use_search=True)
    
    if "error" in result:
        log.error("gemini.research: failed with error: %s", result["error"])
        return result

    # Log text or summary to human logs
    summary = result.get("summary") or result.get("text") or "Research completed."
    human_log.human(f"Research completed: {summary}") # type: ignore[attr-defined]
    
    return result
