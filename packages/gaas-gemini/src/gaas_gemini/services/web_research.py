"""Web research service handler using Gemini with Google Search grounding.

Two-pass approach: the Gemini API cannot combine Google Search grounding
with structured JSON output in a single call.

Pass 1: Grounded search — returns free-text research + source URLs.
Pass 2 (optional): Structured output — reformats research into a schema.
"""

from __future__ import annotations

import logging

from gaas_sdk import runtime
from gaas_sdk.task import TaskRecord

from gaas_gemini.client import GeminiClient

log = logging.getLogger(__name__)


def handle(task: TaskRecord) -> dict:
    """Handle a service.gemini.web_research queue task.

    Receives the full task dict from the worker, consistent with
    platform handlers.  Reads inputs from ``task["payload"]``.

    Payload fields:
        integration: "gemini.{name}"  — integration ID to load config from
        inputs:
            prompt: str               — the research query
            output_schema: dict       — optional JSON schema for structured output
    """
    payload = task["payload"]
    integration_id = payload.get("integration", "")
    inputs = payload.get("inputs", {})
    prompt = inputs.get("prompt", "")

    if not prompt:
        log.warning("web_research called with empty prompt, skipping")
        return {"text": "", "sources": []}

    cfg = runtime.get_integration(integration_id)
    client = GeminiClient(api_key=cfg.api_key, model=getattr(cfg, "model", None) or "gemini-3-pro-preview")

    # Pass 1: Grounded search
    log.info("Gemini web research: %s", prompt[:100])
    text, sources = client.grounded_search(prompt)

    result = {"text": text, "sources": sources}

    # Pass 2: Structured output (optional)
    output_schema = inputs.get("output_schema")
    if output_schema and text:
        structuring_prompt = (
            f"Based on the following research, produce a JSON response "
            f"matching the provided schema.\n\n"
            f"Research:\n{text}\n\n"
            f"Sources:\n" + "\n".join(f"- {s['title']}: {s['url']}" for s in sources)
        )
        try:
            structured = client.structured_output(structuring_prompt, output_schema)
            result["structured"] = structured
        except Exception:
            log.exception("Structured output pass failed, returning raw text")

    source_summary = ", ".join(s.get("title", "?")[:40] for s in sources[:3])
    log.info("Gemini research complete: %d chars, %d sources (%s)",
             len(text), len(sources), source_summary)

    return result
