# Integrations

Integrations connect GaaS to external systems (email, GitHub, Gemini, etc.). The project follows a Home Assistant-inspired model: core integrations for universally useful services, with the intent for community integrations for more bespoke needs.

## Architecture

### Discovery

Integrations are discovered through three channels, checked in this order:

1. **Builtin directory** (`app/integrations/`) - packages shipped in the source tree. Highest priority.
2. **Custom directory** (user-configurable via `directories.custom_integrations` in `config.yaml`) - user-authored integrations that don't touch the source tree. Can shadow entry-point packages.
3. **Entry points** (`gaas.integrations` group) - installable packages that register via `[project.entry-points."gaas.integrations"]` in their pyproject.toml. Lowest priority.

Email and GitHub ship as entry-point packages under `packages/`. They're discovered automatically when installed. A user can shadow them with a local override in the builtin or custom directory during development.

All three channels use the same mechanism: each integration is a Python package with a `manifest.yaml` file. `app/loader.py` parses manifests, builds dynamic Pydantic models from config schemas, and registers handlers.

### Platforms (HA Pattern)

Following Home Assistant's architecture, each integration can have **platforms** -- sub-modules that each handle a specific resource type. The GitHub integration has `pull_requests` and `issues` platforms. The email integration has an `inbox` platform.

Platforms are declared in `manifest.yaml` and each has its own:
- `config_schema` for platform-specific config fields
- `entry_task` for the starting handler
- `const.py` for safety constants (DETERMINISTIC_SOURCES, IRREVERSIBLE_ACTIONS, etc.)
- `templates/` for prompt templates
- Classifications and automations (configured per-platform in `config.yaml`)

Shared config (e.g., orgs/repos for GitHub, IMAP credentials for email) lives at the integration level. Platform-specific config (e.g., `include_mentions`, `limit`) lives under `platforms:` in the config.

### Services

Integrations can declare callable services in their manifest alongside (or instead of) event-driven platforms. A service is a handler that gets invoked from automation `then` clauses, not from a polling schedule.

```yaml
services:
  web_research:
    name: "Web Research"
    description: "Grounded web research using Gemini with Google Search"
    handler: ".services.web_research.handle"
    human_log: "Web research: {{ prompt | truncate(80) }}"
    input_schema:
      properties:
        prompt: { type: string }
      required: [prompt]
```

Services register as `service.{domain}.{service_name}` handlers. The Gemini integration is service-only (`platforms: {}`, one service).

**Human log templates**: Services can declare a `human_log` Jinja2 template in their manifest. This template is rendered at enqueue time and stored in the task payload. When the result is routed, the rendered string appears in the daily audit log instead of the generic "result saved (N chars)" message. Users can override the manifest default per-automation in config via `human_log:` on the service action dict.

**Safety**: Services are irreversible by default, same as scripts. The manifest can declare `reversible: true`, but only for services that are both read-only **and** do not transmit data beyond the system boundary. "Read-only" is necessary but not sufficient -- a service that sends user-context data to an external API is irreversible because you cannot un-send that query. Safety validation enforces `!yolo` for irreversible services triggered from LLM provenance.

Triggered from automations:

```yaml
then:
  - service:
      call: gemini.default.web_research    # {type}.{name}.{service}
      inputs:
        prompt: "research {{ domain }} terms of service"
```

`{{ field }}` references in `inputs` are rendered as Jinja2 templates against the automation context at evaluate time, same as script inputs. Filters, conditionals, and dot-access (e.g. `{{ classification.human }}`) are supported via `SandboxedEnvironment`.

**Result routing**: Service handlers return data (e.g., research text + sources). The worker first stores the return value in the completed task YAML, then routes it via `on_result` descriptors in the task payload. By default, `enqueue_actions()` sets `on_result: [{"type": "note"}]` for all service tasks. This saves the output as a markdown note under `{notes_dir}/services/{domain}/{service_name}/` and writes a human log breadcrumb. Automations can override the default routing:

```yaml
then:
  - service:
      call: gemini.default.web_research
      inputs:
        prompt: "research {{ domain }} terms of service"
      on_result:
        - type: note
          path: research/tos/    # Custom subdirectory under notes_dir
```

The result is also stored in the completed task YAML in `done/` regardless of routing. Service handlers receive the full task dict from the worker and read inputs from `task["payload"]`, consistent with platform handlers.

### Integration Package Structure

```
my_integration/
  manifest.yaml     # Required: metadata + config schema + platforms + services
  __init__.py        # Required: exports HANDLERS dict (aggregated from platforms)
  client.py          # Optional: shared API client used by all platforms
  platforms/
    __init__.py
    my_platform/
      __init__.py    # Exports platform HANDLERS dict
      const.py       # Safety constants (DETERMINISTIC_SOURCES, etc.)
      check.py       # Entry task handler
      collect.py     # Data collection handler
      classify.py    # LLM classification handler
      evaluate.py    # Automation evaluation handler
      act.py         # Action execution handler
      store.py       # NoteStore wrapper for this resource type
      templates/     # Jinja2 prompt templates
  services/          # Optional: service handlers
    __init__.py
    my_service.py    # Service handler function
```

### manifest.yaml

```yaml
domain: my_integration          # Must match directory/package name
name: "My Integration"          # Human-readable display name
version: "1.0.0"
entry_task: check               # Default entry task (overridden per platform)
dependencies:                   # pip dependencies (checked at startup, not auto-installed)
  - some-library>=1.0
config_schema:                  # JSON Schema for shared integration config fields
  properties:
    api_url:
      type: string
  required:
    - api_url
platforms:
  my_platform:
    name: "My Platform"
    entry_task: check
    config_schema:              # JSON Schema for platform-specific config fields
      properties:
        polling_limit:
          type: integer
          default: 50
      required: []
services:                       # Optional: callable services
  my_service:
    name: "My Service"
    description: "What this service does"
    handler: ".services.my_service.handle"
    reversible: false           # Default. Only set true for local-only, read-only services
    human_log: "My service: {{ query | truncate(80) }}"  # Optional: Jinja2 template for human log
    input_schema:
      properties:
        query: { type: string }
      required: [query]
```

### Handler Registration

Each platform exports a `HANDLERS` dict. The integration `__init__.py` aggregates them with platform prefixes:

```python
# my_integration/platforms/my_platform/__init__.py
HANDLERS = {
    "check": check_handle,
    "collect": collect_handle,
}

# my_integration/__init__.py
from .platforms.my_platform import HANDLERS as platform_handlers

HANDLERS = {}
for suffix, handler in platform_handlers.items():
    HANDLERS[f"my_platform.{suffix}"] = handler
```

`app/integrations/__init__.py` calls `register_all()` at startup, which prefixes handlers with the domain name (e.g., `email.inbox.check`, `github.pull_requests.classify`). Entry tasks are keyed per platform: `ENTRY_TASKS["github.pull_requests"] = "github.pull_requests.check"`. Service handlers are registered as `service.{domain}.{service_name}`.

### Entry Tasks

Each platform has its own entry task (declared in `manifest.yaml` under `platforms:`). The scheduler and API endpoint enqueue entry tasks for each enabled platform within an integration.

### Task Flow

Platforms define their own task flow. The standard pattern is `check -> collect -> classify -> evaluate -> act`, but there is no mandatory pipeline. Tasks enqueue downstream tasks with appropriate priorities:
- Priority 3: Discovery/collection (get data quickly)
- Priority 5: Default
- Priority 6: Classification (process after collection)
- Priority 7: Actions (execute after classification)
- Priority 9: Low confidence items (unauthenticated emails)

## Classification System

Classifications are LLM-driven assessments defined per-platform in `config.yaml`. Three types:

| Type | Schema | Config condition syntax |
|------|--------|----------------------|
| `confidence` | `{"type": "number"}` (0-1 float) | Numeric threshold (`0.8`), operator string (`">0.8"`, `"<=0.5"`) |
| `boolean` | `{"type": "boolean"}` | `true` / `false` (identity comparison with `is`) |
| `enum` | `{"type": "string", "enum": [...]}` | Exact string or list for any-of match |

Classifications are fed to the LLM as a JSON schema, and the response is validated against that schema with up to 3 retries.

## Automation Dispatch: The Safety Boundary

The automation dispatch layer (`evaluate_automations` in `gaas_sdk.evaluate`) is **purely deterministic**. It evaluates `when`/`then` rules against classification results and produces a list of actions. This is the critical safety boundary:

- The LLM is non-deterministic and its output is treated as untrusted
- The dispatch layer is deterministic and is where bugs become irreversible actions
- Tests focus on this layer, not on LLM output

When conditions use AND semantics. All conditions in a `when` dict must match. Missing keys in the result cause the automation to not fire (safe default).

### Shared Action Layer

Some actions are cross-cutting -- they can be triggered from any integration's automations. The evaluate phase partitions actions via `enqueue_actions()` from `gaas_sdk.actions`:

- **Script actions** (`{"script": {"name": "...", "inputs": {...}}}`) are enqueued as individual `script.run` queue tasks with resolved inputs.
- **Service actions** (`{"service": {"call": "...", "inputs": {...}}}`) are enqueued as individual `service.{domain}.{service_name}` queue tasks with default `on_result` routing (note + human log).
- **Platform actions** (strings like `"archive"`, dicts like `{"draft_reply": "..."}`) are bundled into a single platform act task as before.

Each platform's `evaluate.py` calls `enqueue_actions()` instead of `runtime.enqueue()` directly. The partitioning is transparent to the rest of the pipeline. Service actions can include `on_result` in their config to override default result routing.

## Prompt Templates

Templates live in `templates/` subdirectories within each platform (Jinja2 `.jinja` files). Injection defense pattern:

1. Generate random salt markers (`secrets.token_hex(4).upper()`)
2. Wrap untrusted content between salt markers
3. Instruct the LLM that content between markers is untrusted
4. The template itself says "Ignore all previous instructions" after the untrusted block, a second barrier

This is dual-barrier defense: even if the prompt barrier fails, the deterministic dispatch layer prevents unsafe actions.

## Importing from the SDK

Integration code imports from `gaas_sdk.*`, not from `app.*`:

```python
# Config models
from gaas_sdk.models import ClassificationConfig, AutomationConfig

# Evaluation engine
from gaas_sdk.evaluate import evaluate_automations, conditions_match

# Classification utilities
from gaas_sdk.classify import build_schema, make_jinja_env

# NoteStore
from gaas_sdk.store import NoteStore

# Runtime functions (enqueue, config lookup, LLM)
from gaas_sdk import runtime
runtime.enqueue(payload, priority=5)
runtime.get_integration(integration_id)
runtime.create_llm_conversation(model="default", system="...")

# Action partitioning
from gaas_sdk.actions import enqueue_actions

# Logging (use this instead of logging.getLogger)
from gaas_sdk.logging import get_logger
log = get_logger(__name__)
```

## Adding a New Integration

### Installable package (recommended)

1. Create a package under `packages/your_integration/` with a `src/` layout
2. Add a `pyproject.toml` with entry point: `[project.entry-points."gaas.integrations"]` -> `your_domain = "your_package"`
3. Add a `manifest.yaml` with `domain`, `config_schema`, and `platforms:` and/or `services:` sections
4. Add an `__init__.py` that aggregates `HANDLERS` from platforms
5. Create `platforms/` and/or `services/` sub-packages
6. Import from `gaas_sdk.*` for models, evaluation, runtime functions
7. Categorize every action by reversibility tier before implementing
8. Add tests in your package's `tests/` directory, importing from `gaas_sdk.*` directly
9. Add the package to root `pyproject.toml` dependencies and `[tool.uv.sources]`
10. Add to your `config.yaml` using `type: <domain>` with a `platforms:` section
11. If using LLM classification, add prompt templates with salt-based injection defenses

### Custom (external) integration

1. Create a package directory under your `custom_integrations` path
2. Add a `manifest.yaml` with `domain`, `config_schema`, and `platforms:` section
3. Add an `__init__.py` that aggregates `HANDLERS` from platforms
4. Create a `platforms/` directory with a sub-package per resource type
5. Each platform exports a `HANDLERS` dict from its `__init__.py`
6. Install any dependencies declared in `manifest.yaml` with `uv add`
7. Add the integration to your `config.yaml` using `type: <domain>` with a `platforms:` section
8. Restart GaaS. The integration is discovered automatically
