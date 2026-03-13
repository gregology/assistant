# Integration Contract

This is the spec. Everything here is required unless marked optional.

## Package layout

Platform integration:

```
packages/assistant-{domain}/
  pyproject.toml
  src/assistant_{domain}/
    __init__.py
    manifest.yaml
    client.py                     # API client (if needed)
    platforms/
      {platform_name}/
        __init__.py
        const.py                  # Safety constants (required)
        check.py                  # Entry task handler
        collect.py                # Data fetching handler
        classify.py               # LLM classification handler
        evaluate.py               # Automation evaluation handler
        act.py                    # Action execution handler
        store.py                  # NoteStore subclass (if needed)
        templates/
          classify.jinja          # Prompt template
  tests/
    test_*.py
```

Service integration:

```
packages/assistant-{domain}/
  pyproject.toml
  src/assistant_{domain}/
    __init__.py
    manifest.yaml
    client.py                     # API client (if needed)
    services/
      __init__.py
      {service_name}.py           # Service handler
  tests/
    test_*.py
```

No `__init__.py` in test directories. pytest uses `--import-mode=importlib` and discovers tests without them. Putting one in would cause mypy to choke on duplicate `tests` modules across packages.

The source `__init__.py` files can be empty. Handler loading goes through the manifest, not through Python imports from `__init__`.

## pyproject.toml

```toml
[project]
name = "assistant-{domain}"
version = "0.1.0"
description = "Short description"
requires-python = ">=3.11"
dependencies = [
    "assistant-sdk>=0.1.0",
    # your dependencies here
]

[project.entry-points."assistant.integrations"]
{domain} = "assistant_{domain}"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/assistant_{domain}"]
```

The entry point key must match your `domain` in `manifest.yaml`. The value is the importable module name.

You also need to add the package to the root `pyproject.toml`:
- Add `assistant-{domain}` to `[project.dependencies]` (or as an optional extra if the integration has heavy deps like `google-genai`)
- Add a source mapping under `[tool.uv.sources]` pointing to the local path

## manifest.yaml

This is how Assistant discovers your integration. Lives at `src/assistant_{domain}/manifest.yaml`.

### Required fields

```yaml
domain: {domain}                    # Lowercase, no hyphens. Must match entry point key.
name: "Human-Readable Name"        # Display name
version: "0.1.0"
entry_task: check                   # Default entry task for platforms. "" for service-only.
```

### config_schema

JSON Schema for integration-level config fields. These appear directly under the integration block in `config.yaml`.

```yaml
config_schema:
  properties:
    api_key:
      type: string
    base_url:
      type: string
      default: "https://api.example.com"
    timeout:
      type: integer
      default: 30
  required:
    - api_key
```

Supported types: `string`, `integer`, `boolean`, `array` (with `items`), `object`. The system maps these to Python types and builds a Pydantic model at startup. If you provide a `default`, the field is optional in config.

### dependencies

External Python packages the integration needs. The loader checks these at startup and skips the integration (with a warning) if any are missing. These are not auto-installed.

```yaml
dependencies:
  - some-api-client>=2.0
  - lxml
```

### platforms

For platform integrations. Each platform has a name, entry task, handlers, and optional config schema.

```yaml
platforms:
  {platform_name}:
    name: "Human Name"
    entry_task: check               # Usually "check"
    handlers:
      check: ".platforms.{platform_name}.check.handle"
      collect: ".platforms.{platform_name}.collect.handle"
      classify: ".platforms.{platform_name}.classify.handle"
      evaluate: ".platforms.{platform_name}.evaluate.handle"
      act: ".platforms.{platform_name}.act.handle"
    config_schema:
      properties:
        limit:
          type: integer
          default: 50
      required: []
```

Handler paths starting with `.` are relative to the integration module. The loader resolves them at import time.

An integration can have multiple platforms. GitHub has `pull_requests` and `issues` -- they share a client but have independent pipelines, classifications, and automations.

For service-only integrations, set `platforms: {}`.

### services

For service integrations. Each service declares a handler, input schema, and reversibility.

```yaml
services:
  {service_name}:
    name: "Human Name"
    description: "What this service does"
    handler: ".services.{service_name}.handle"
    reversible: false               # Default. true only for local-only read-only services.
    human_log: "Did the thing: {{ prompt | truncate(80) }}"  # Jinja2 template, optional
    input_schema:
      properties:
        prompt:
          type: string
        options:
          type: object
      required:
        - prompt
```

The `human_log` template is rendered at enqueue time and stored in the task payload. It shows up in the daily audit log. If omitted, a generic message is used.

## Handler signatures

All handlers receive the full task dict from the worker.

### Platform handlers

```python
def handle(task: dict):
    """Platform handlers return None. They enqueue downstream tasks."""
    payload = task["payload"]
    integration_id = payload["integration"]

    integration = runtime.get_integration(integration_id)
    platform = runtime.get_platform(integration_id, "{platform_name}")

    # do work...

    runtime.enqueue({
        "type": "{domain}.{platform}.{next_stage}",
        "integration": integration_id,
        # stage-specific fields
    }, priority=5)
```

### Service handlers

```python
def handle(task: dict) -> dict:
    """Service handlers return a result dict. The worker routes it."""
    payload = task["payload"]
    integration_id = payload.get("integration", "")
    inputs = payload.get("inputs", {})

    # do work...

    return {"text": "result", "extra_field": "whatever"}
```

The returned dict gets saved as a markdown note (default behavior). Keys become frontmatter fields. You can return whatever makes sense for your service.

## const.py (platforms only)

Every platform needs a `const.py` with these four exports:

```python
from assistant_sdk.models import ClassificationConfig

DEFAULT_CLASSIFICATIONS: dict[str, ClassificationConfig] = {
    # What questions would a human ask when triaging these items?
    "importance": ClassificationConfig(
        prompt="how important is this?",
        type="confidence",
    ),
    "actionable": ClassificationConfig(
        prompt="can I take action on this right now?",
        type="boolean",
    ),
    "category": ClassificationConfig(
        prompt="what category does this fall into?",
        type="enum",
        values=["bug", "feature", "question", "other"],
    ),
}

# Fields that come from the source directly, not from LLM classification.
# Used by the provenance system to determine if an automation is
# deterministic ("rule") or LLM-influenced ("llm"/"hybrid").
DETERMINISTIC_SOURCES: frozenset[str] = frozenset({
    "author",
    "channel",
    "has_attachments",
    # ... every field in your snapshot that isn't from the LLM
})

# Actions that cannot be undone. These are blocked from LLM/hybrid
# provenance at config load time unless tagged with !yolo.
IRREVERSIBLE_ACTIONS: frozenset[str] = frozenset({
    # "delete",  -- example
})

# Allowlist of string actions your act handler accepts.
# Unknown strings are silently skipped. This set must not grow
# without a reversibility review.
SIMPLE_ACTIONS: frozenset[str] = frozenset({
    # "archive", "pin", "mute",  -- examples
})
```

It's fine for `IRREVERSIBLE_ACTIONS` and `SIMPLE_ACTIONS` to start empty. GitHub's are both empty because it's read-only right now. Add actions as you implement them, one at a time, with a reversibility assessment for each.

## Runtime API

Everything your integration needs is in `assistant_sdk.runtime`:

```python
from assistant_sdk import runtime

# Queue a task. Returns task ID or None if rejected by policy.
task_id = runtime.enqueue(payload_dict, priority=5, provenance="rule")

# Look up config.
integration = runtime.get_integration("domain.instance_name")
platform = runtime.get_platform("domain.instance_name", "platform_name")

# LLM access.
conversation = runtime.create_llm_conversation(model="default", system="You are...")
response = conversation.chat_json(prompt, schema)

# Config and paths.
llm_config = runtime.get_llm_config("default")
notes_dir = runtime.get_notes_dir()
```

The integration ID is `{type}.{name}` from the user's `config.yaml`. So `type: slack, name: work` becomes `slack.work`.

## Task priorities

Convention across existing integrations:

- **3**: Collection tasks (fast, lightweight)
- **5**: Default / entry tasks
- **6**: Classification (after data is collected)
- **7**: Actions (after classification and evaluation)
- **9**: Low-confidence or low-priority items

Lower number = higher priority. The worker always picks the lowest-numbered pending task.

## Tests

Tests live in `packages/assistant-{domain}/tests/` and import from `assistant_sdk.*` directly. No `app.*` imports. They run without the app config singleton.

```bash
uv run pytest packages/assistant-{domain}/tests/ -v
```

What to test, in priority order:

1. **Safety constants** -- verify `IRREVERSIBLE_ACTIONS` and `SIMPLE_ACTIONS` are correct for your actions
2. **Condition matching** -- your `resolve_value` callable handles all snapshot fields correctly, returns `MISSING` for absent keys
3. **Action execution** -- your act handler enforces the `SIMPLE_ACTIONS` allowlist, skips unknowns
4. **Data parsing** -- your collect handler correctly parses API responses into note frontmatter
5. **Client** -- your API client handles errors, timeouts, rate limits

Test rigor scales with irreversibility. A read-only integration needs less coverage than one that deletes things.

See `tests/safety/` in the root for how provenance and automation invariant tests work. Those tests cover your integration automatically once your `const.py` exports the right sets.
