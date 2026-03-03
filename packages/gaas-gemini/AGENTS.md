# gaas-gemini

A service-only integration for grounded web research via Google's Gemini API. No platforms, no event-driven pipeline. Provides a callable service that other integrations can invoke from automation `then` clauses.

Optional dependency: `pip install gaas[gemini]` or `uv add gaas-gemini`.

## Structure

```
src/gaas_gemini/
  __init__.py
  client.py                  # GeminiClient wrapping google-genai SDK
  manifest.yaml              # Service-only manifest (platforms: {})
  services/
    __init__.py
    web_research.py           # Two-pass service handler
```

## Two-pass research pattern

The Gemini API cannot combine Google Search grounding with structured JSON output in a single call. So `web_research` runs two passes:

**Pass 1** (`client.grounded_search`): Calls Gemini with the `GoogleSearch` tool enabled. Returns free-text research and a list of source URLs extracted from `grounding_metadata.grounding_chunks`.

**Pass 2** (`client.structured_output`): Takes the research text from Pass 1 and reformats it into a caller-provided JSON schema using `response_mime_type="application/json"` + `response_schema`. Only runs if `output_schema` is provided in the task inputs.

This is encapsulated in `GeminiClient` so the handler stays simple.

## Service declaration

From `manifest.yaml`:

```yaml
platforms: {}
services:
  web_research:
    name: "Web Research"
    handler: ".services.web_research.handle"
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
        prompt: "research $domain terms of service"
```

## Result handling

The handler receives the full task dict from the worker and reads inputs from `task["payload"]` (consistent with platform handlers). It returns `{"text": str, "sources": list, "structured": dict | None}`.

The worker captures the return value, routes it via `on_result` (default: save as markdown note + human log breadcrumb), and stores it in the completed task YAML. Results are saved to `{notes_dir}/services/gemini/web_research/` as markdown files with frontmatter.

## Tests

```
tests/
  test_client.py             # Mocked google.genai.Client, verifies two-pass flow
  test_web_research.py        # Mocked GeminiClient, tests handler with/without output_schema
```

Run in isolation:

```bash
uv run pytest packages/gaas-gemini/tests/
```

All tests mock the Gemini API. No real API calls in the default test suite.

## When modifying

- The `google-genai` SDK is the only external dependency beyond gaas-sdk.
- If Gemini ever supports grounding + structured output in one call, the two-pass pattern can collapse to one. Update `client.py` and the handler, keep the same public interface.
- New services go in `services/` with their own handler module and a corresponding entry in `manifest.yaml`.
