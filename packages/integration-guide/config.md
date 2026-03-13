# Config Patterns

Assistant uses Home Assistant-style YAML config. This doc covers how your integration plugs into it.

## How users configure your integration

In their `config.yaml`, users add a block under `integrations:`:

```yaml
integrations:
  - type: {domain}              # Matches your manifest domain
    name: myinstance            # User-chosen name. With type, forms the ID "{domain}.myinstance"
    api_key: !secret my_api_key # Credential reference
    schedule:                   # Optional: when to poll
      every: 30m
    llm: default                # Which LLM profile to use for classification
    platforms:
      {platform_name}:
        # platform-specific config fields
        classifications:
          # what to classify
        automations:
          # what to do about it
```

You don't write this file. You define the `config_schema` in your manifest and the system generates a Pydantic model from it at startup. The user fills in their values.

## config_schema

JSON Schema syntax. The system maps types like this:

| JSON Schema | Python type |
|-------------|-------------|
| `string` | `str` |
| `integer` | `int` |
| `boolean` | `bool` |
| `array` (with `items: {type: string}`) | `list[str]` |
| `object` | `dict` |

Fields with a `default` become optional. Fields in `required` are mandatory.

Integration-level schema (credentials, connection details):

```yaml
config_schema:
  properties:
    api_key:
      type: string
    base_url:
      type: string
      default: "https://api.example.com"
  required:
    - api_key
```

Platform-level schema (polling options, display preferences):

```yaml
platforms:
  messages:
    config_schema:
      properties:
        limit:
          type: integer
          default: 100
        channels:
          type: array
          items:
            type: string
      required: []
```

At runtime your handlers access these as attributes on the integration/platform config objects:

```python
integration = runtime.get_integration("slack.work")
url = integration.base_url     # "https://api.example.com"

platform = runtime.get_platform("slack.work", "messages")
limit = platform.limit         # 100
channels = platform.channels   # ["general", "random"]
```

## !secret references

Credentials don't go in `config.yaml`. They go in a separate `secrets.yaml` file:

```yaml
# secrets.yaml
my_api_key: xoxb-1234567890
```

```yaml
# config.yaml
api_key: !secret my_api_key
```

The `!secret` YAML constructor resolves these at load time. Your integration code never sees the reference -- just the resolved value. You don't need to handle this yourself.

## Classifications

Users define what the LLM should assess for each item. Three types:

**Confidence** (default): Returns a float between 0 and 1.

```yaml
classifications:
  human: "is this written by a human being?"     # Shorthand form
  importance:                                      # Expanded form
    prompt: "how important is this?"
    type: confidence
```

The shorthand (bare string) expands to `{prompt: "...", type: "confidence"}` automatically.

**Boolean**: Returns true or false.

```yaml
classifications:
  actionable:
    prompt: "can I take action on this right now?"
    type: boolean
```

**Enum**: Returns one of a fixed set of values.

```yaml
classifications:
  category:
    prompt: "what category does this fall into?"
    type: enum
    values: [bug, feature, question, other]
```

Your `const.py` defines `DEFAULT_CLASSIFICATIONS` -- sensible defaults that apply when the user doesn't configure their own. The user can override any or all of them in their config.

## Automations

`when`/`then` pairs. All conditions in `when` must match (AND semantics). If any condition is missing from the data, the automation doesn't fire.

### Condition operators

**Confidence fields** -- threshold expressions:

```yaml
- when:
    classification.importance: ">= 0.8"    # Operator + value as string
    classification.importance: 0.8          # Bare number means >= 0.8
```

Supported operators: `>=`, `>`, `<=`, `<`, `==`.

**Boolean fields** -- identity match:

```yaml
- when:
    is_thread: true
    classification.actionable: false
```

**Enum fields** -- exact match or list match:

```yaml
- when:
    classification.category: "bug"                    # Exact match
    classification.category: ["bug", "feature"]       # Any of these
```

**Deterministic fields** -- same rules, but these establish `rule` provenance:

```yaml
- when:
    channel: "alerts"                # Exact string match
    channel: ["alerts", "ops"]       # Any of these
    has_attachments: true             # Boolean identity
```

### Actions (the `then` clause)

String actions are the simplest. They map to methods on your domain object:

```yaml
then: archive                       # Single action (auto-wrapped in list)
then: [archive, pin]                # Multiple actions
```

Dict actions for parameterized operations:

```yaml
then:
  - draft_reply: "Thanks, I'll look into this"
  - move_to: "reviewed"
```

Script actions invoke user-defined shell scripts:

```yaml
then:
  - script:
      name: notify_team
      inputs:
        message: "New {{ classification.category }} from {{ author }}"
```

Service actions call service integrations:

```yaml
then:
  - service:
      call: gemini.default.web_research
      inputs:
        prompt: "research {{ domain }}"
      on_result:                              # Optional, default saves as note
        - type: note
          path: research/
```

### Jinja2 in inputs

Script and service `inputs` support Jinja2 templates. Variables come from your platform's `resolve_value` callable -- snapshot fields and classification results.

```yaml
inputs:
  prompt: "{{ from_address }} sent a {{ classification.category }} about {{ subject }}"
```

Missing variables render as empty strings (not errors). The template engine uses a sandboxed environment.

## Schedule formats

For platform integrations that poll:

```yaml
schedule:
  every: 30m         # Shorthand interval: 30m, 2h, 1d
  # OR
  cron: "0 8-18 * * 1-5"   # Standard cron expression (weekdays 8am-6pm)
```

Pick one. `every` gets converted to a cron expression internally. `every: 30m` becomes `*/30 * * * *`.

Service integrations don't have schedules. They're triggered by automation rules from other integrations.

## LLM profiles

Users define LLM backends under `llms:` in their config:

```yaml
llms:
  default:
    base_url: http://localhost:11434
    model: llama3:latest
    parameters:
      temperature: 0.7
  fast:
    base_url: http://localhost:11434
    model: phi3:latest
```

Your integration references a profile by name via the `llm` field. It defaults to `"default"`. The classification handler uses `runtime.create_llm_conversation(model=integration.llm)` to get a conversation instance configured for the right backend.

## Queue policies

Users can configure dedup and rate limiting per task type:

```yaml
queue_policies:
  defaults:
    deduplicate_pending: true
  overrides:
    service.gemini.web_research:
      rate_limit:
        max: 10
        per: 1h
```

This is transparent to your integration code. The `runtime.enqueue()` call returns `None` if a task is rejected by policy. Your handler doesn't need to check for this.
