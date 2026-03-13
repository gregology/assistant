# assistant-gemini

A service-only integration for grounded web research via Google's Gemini API. No platforms, no event-driven pipeline. Provides a callable service that other integrations can invoke from automation `then` clauses.

Installed as a core dependency via `uv sync`.

## Structure

```
src/assistant_gemini/
  __init__.py
  client.py                  # GeminiClient wrapping google-genai SDK
  manifest.yaml              # Service-only manifest (platforms: {})
  services/
    __init__.py
    web_research.py           # Two-pass service handler
```

## Service declaration

From `manifest.yaml`:

```yaml
platforms: {}
services:
  web_research:
    name: "Web Research"
    handler: ".services.web_research.handle"
    human_log: "Web research: {{ prompt | truncate(80) }}"
    input_schema:
      properties:
        prompt: { type: string }
        output_schema: { type: object }
      required: [prompt]
```

Web research is **irreversible** (the default). Although it doesn't modify local state, it sends user-context data to an external API -- you cannot un-send that query. The prompt may contain information extracted from emails or other private sources, and an LLM misclassification could cause unexpected data to be transmitted. Requires `!yolo` when triggered from LLM provenance.

Triggered from automations:

```yaml
then:
  - !yolo
    service:
      call: gemini.default.web_research
      inputs:
        prompt: "research {{ domain }} terms of service"
```

## Result handling

The handler receives the full task dict from the worker and reads inputs from `task["payload"]` (consistent with platform handlers). It returns `{"text": str, "sources": list, "structured": dict | None}`.

The worker stores the return value in the completed task YAML first, then routes it via `on_result` (default: save as markdown note + human log breadcrumb). Results are saved to `{notes_dir}/services/gemini/web_research/` as markdown files with frontmatter. If routing fails, the result is still preserved in `done/`.

## Tests

```
tests/
  test_client.py             # Mocked google.genai.Client, verifies two-pass flow
  test_web_research.py        # Mocked GeminiClient, tests handler with/without output_schema
```

Run in isolation:

```bash
uv run pytest packages/assistant-gemini/tests/
```

All tests mock the Gemini API. No real API calls in the default test suite.

## When modifying

- The `google-genai` SDK is the only external dependency beyond assistant-sdk.
- New services go in `services/` with their own handler module and a corresponding entry in `manifest.yaml`.
