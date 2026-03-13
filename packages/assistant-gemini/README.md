# assistant-gemini

Web research using Google's Gemini API with Google Search grounding. Unlike email or GitHub, this isn't an event-driven poller. It's a callable service that other integrations trigger from automation `then` clauses.

## Prerequisites

- A Google AI API key ([aistudio.google.com](https://aistudio.google.com/))

## Installation

Gemini is included when you install Assistant:

```bash
uv sync
```

## Config

```yaml
integrations:
  - type: gemini
    name: default
    api_key: !secret gemini_api_key
    # model: gemini-3-pro-preview       # Optional, uses SDK default if omitted
```

`api_key` is required. `model` is optional.

## Calling from automations

You can trigger the service from any integration's automation rules:

```yaml
# In an email integration's automations:
automations:
  - when:
      classification.user_agreement_update: true
    then:
      - archive
      - !yolo
        service:
          call: gemini.default.web_research
          inputs:
            prompt: "research {{ domain }} terms of service changes"
```

The `call` format is `{type}.{name}.{service_name}`. If you named your Gemini integration `research` instead of `default`, the call would be `gemini.research.web_research`.

`{{ field }}` references in `inputs` are rendered as Jinja2 templates against the automation context at runtime, same as script inputs. Filters (`{{ domain | upper }}`), conditionals (`{% if ... %}`), and dot-access (`{{ classification.human }}`) are supported.

### Input schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | Yes | The research query |
| `output_schema` | object | No | JSON schema for structured output (triggers a second pass to reformat results) |

Without `output_schema` you get free-text research results plus source URLs. With it, the service makes a second Gemini call to restructure the research into your schema.

## Research output

When the service completes, the results are saved as a markdown note under your notes directory and a breadcrumb is written to the daily human log.

**Where results are saved:**

```
{notes_dir}/services/gemini/web_research/
  2026_03_03__14_25_32__a1b2c3d4.md
  2026_03_04__09_10_00__b2c3d4e5.md
```

**What the note looks like:**

```yaml
---
service: service.gemini.web_research
integration: gemini.default
inputs:
  prompt: "research example.com terms of service changes"
completed_at: "2026-03-03T14:25:32+00:00"
sources:
  - title: "Example.com ToS"
    url: "https://example.com/tos"
  - title: "ToS Tracker"
    url: "https://tostracker.example.com/example.com"
structured:                                # Only present if output_schema was provided
  summary: "The ToS was updated on..."
---
Example.com recently updated their terms of service. The key changes include...

(full research text as markdown body)
```

**What appears in the daily log** (`logs/2026-03-03 Tuesday.md`):

```
 - 14:25 Web research: research example.com terms of service changes -> services/gemini/web_research/2026_03_03__14_25_32__a1b2c3d4.md
```

The log message comes from the `human_log` template in the manifest (`"Web research: {{ prompt | truncate(80) }}"`). You can override it per-automation by adding `human_log:` to the service action in your config:

```yaml
- service:
    call: gemini.default.web_research
    inputs:
      prompt: "research {{ domain }} terms of service changes"
    human_log: "ToS update for {{ domain }}"
```

The full result is also stored in the completed task YAML in `data/queue/done/` for audit purposes.

### Custom output routing

By default, service results go to `services/{domain}/{service_name}/` under your notes directory. You can override this per-automation with `on_result`:

```yaml
automations:
  - when:
      classification.user_agreement_update: true
    then:
      - service:
          call: gemini.default.web_research
          inputs:
            prompt: "research {{ domain }} terms of service changes"
          on_result:
            - type: note
              path: research/tos_updates/
```

This saves the research note to `{notes_dir}/research/tos_updates/` instead.

## Safety

The `web_research` service is **irreversible** (the default). Although it doesn't modify local state, it sends user-context data to Google's Gemini API -- you cannot un-send that query. The prompt may contain information extracted from emails or other private sources (e.g., an LLM misclassifies a password as an acronym and sends it for research). Because the data leaves the system boundary, this is not reversible.

When triggered from LLM-provenance automations, `!yolo` is required:

```yaml
- !yolo
  service:
    call: gemini.default.web_research
    inputs:
      prompt: "research {{ domain }} terms of service"
```

From deterministic provenance (e.g., `when: {domain: "example.com"}`), no `!yolo` is needed.
