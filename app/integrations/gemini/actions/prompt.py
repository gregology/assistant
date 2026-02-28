import logging
from typing import Any
from app.config import config
from ..client import GeminiClient

log = logging.getLogger(__name__)

def handle(task: dict[str, Any]) -> dict[str, Any]:
    """Execute the gemini.prompt action."""
    payload = task.get("payload", {})
    inputs = payload.get("inputs", {})
    prompt = inputs.get("prompt")
    schema = inputs.get("schema") # Optional schema
    
    if not prompt:
        raise ValueError("gemini.prompt: 'prompt' input is required")

    gemini_config = next((i for i in config.integrations if i.type == "gemini"), None)
    if not gemini_config:
        raise ValueError("gemini.prompt: No 'gemini' integration found in configuration")

    api_key = getattr(gemini_config, "api_key", None)
    model = getattr(gemini_config, "model", "gemini-2.0-pro-exp-02-05")
    
    if not api_key:
        raise ValueError(f"gemini.prompt: API key not found for integration '{gemini_config.name}'")

    log.info("gemini.prompt: executing prompt using model=%s", model)
    
    client = GeminiClient(api_key=api_key, model_name=model)
    return client.generate(prompt=prompt, response_schema=schema, use_search=False)
