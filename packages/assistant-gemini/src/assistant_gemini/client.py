"""Gemini API client wrapping google-genai SDK."""

from __future__ import annotations

import json
import logging

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-pro-preview"


class GeminiClient:
    """Client for Google Gemini API with grounding and structured output."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def grounded_search(self, query: str) -> tuple[str, list[dict]]:
        """Perform a grounded search using Gemini with Google Search.

        Returns (text_response, sources) where sources is a list of
        dicts with 'title' and 'url' keys extracted from grounding metadata.
        """
        response = self._client.models.generate_content(
            model=self._model,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )

        text = response.text or ""
        sources = []

        # Extract grounding sources from metadata
        for candidate in response.candidates or []:
            metadata = getattr(candidate, "grounding_metadata", None)
            if metadata is None:
                continue
            for chunk in getattr(metadata, "grounding_chunks", []) or []:
                web = getattr(chunk, "web", None)
                if web:
                    sources.append(
                        {
                            "title": getattr(web, "title", ""),
                            "url": getattr(web, "uri", ""),
                        }
                    )

        return text, sources

    def structured_output(self, prompt: str, schema: dict) -> dict:
        """Generate structured JSON output using Gemini.

        The Gemini API cannot combine Google Search grounding with
        structured output in a single call, so this is used as a
        second pass after grounded_search().
        """
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )

        return json.loads(response.text)
