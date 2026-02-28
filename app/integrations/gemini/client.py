from google import genai
from google.genai import types
import logging
import json
from typing import Any

log = logging.getLogger(__name__)

class GeminiClient:
    def __init__(self, api_key: str, model_name: str = "gemini-2.0-pro-exp-02-05"):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def generate(
        self, 
        prompt: str, 
        response_schema: dict[str, Any] | None = None, 
        use_search: bool = False
    ) -> dict[str, Any]:
        """
        Generic Gemini generation call with optional search grounding and structured output.
        """
        log.info("GeminiClient: Generating content using %s (search=%s)", self.model_name, use_search)

        tools = []
        if use_search:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        config_args = {
            "tools": tools if tools else None,
            "temperature": 0.2
        }

        if response_schema:
            config_args["response_mime_type"] = "application/json"
            config_args["response_schema"] = response_schema

        config = types.GenerateContentConfig(**config_args)

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config
            )
            
            if response_schema:
                if hasattr(response, 'parsed') and response.parsed:
                    if hasattr(response.parsed, 'model_dump'):
                        return response.parsed.model_dump()
                    return response.parsed
                
                try:
                    return json.loads(response.text)
                except json.JSONDecodeError:
                    log.error("GeminiClient: Failed to parse JSON response: %s", response.text)
                    return {"error": "Could not parse AI response as JSON", "raw": response.text}
            
            return {"text": response.text}
                
        except Exception as e:
            log.exception("GeminiClient: API call failed")
            return {"error": str(e)}
