# App Architecture

The app runs as two processes: a **FastAPI server** (API + cron scheduler) and a **worker** (task queue consumer). They communicate through the filesystem-based task queue.

## Components

### FastAPI Server (`main.py`)

Endpoints:
- `GET /` - Health check
- `GET /integrations` - List configured integrations with their composite IDs and enabled platforms
- `POST /integrations/{integration_id}/run` - Trigger entry tasks for all enabled platforms
- `POST /integrations/{integration_id}/{platform}/run` - Trigger entry tasks for a specific platform

The `{integration_id}` is a composite ID in `{type}.{name}` format (e.g. `github.my_repos`), following HA's entity_id pattern. It's a computed property on `BaseIntegrationConfig`, never stored in YAML.

The scheduler (`scheduler.py`) runs inside the FastAPI process, converting `every: 30m` or `cron:` expressions from config into periodic task enqueues. Schedules are created per-platform within each integration.

### Task Queue (`queue.py`) and Queue Policies (`queue_policy.py`)

See `app/AGENTS.yaml` for design decisions and invariants.

### Runtime Init (`runtime_init.py`)

Registers app-level implementations with `assistant_sdk.runtime` at startup. Called from `main.py` and `worker.py` before `load_all_modules()`. Wires up `policy_enqueue` (config-driven dedup + rate limiting wrapper around `queue.enqueue`), `config.get_integration`, `config.get_platform`, LLM conversation creation, and notes directory lookup. Tests register via `conftest.py`.

### NoteStore (`assistant_sdk.store`)

The `NoteStore` implementation lives in the SDK package. All persistent data (emails, PRs) uses this pattern:

```yaml
---
uid: "12345"
from_address: sender@example.com
subject: Hello
classification:
  human: 0.85
  requires_response: true
---
(optional body content)
```

Platform-specific stores (`EmailStore`, `PullRequestStore`, `IssueStore`) wrap `NoteStore` with domain-specific methods. Active notes live in the root directory. Notes no longer requiring attention are moved to `synced/`.

### LLM Abstraction (`llm.py`)

- **`LLMBackend` protocol**: Any object with a `chat()` method works. `LlamaCppBackend` is the default, using the OpenAI-compatible `/v1/chat/completions` endpoint.
- **`LLMConversation`**: Manages multi-turn conversations. Supports plain text and structured (JSON schema-validated) output with automatic retry (3 attempts).
- **Backend-agnostic**: Config defines named LLM profiles (`default`, `fast`, etc.) with different `base_url`, `model`, `token`, and `parameters`. Integrations reference profiles by name.
- **Schema validation**: Uses `jsonschema.Draft202012Validator`. On structured output failure, removes the dangling user message and raises `SchemaValidationError`.

### Human Log (`human_log.py`)

Registers the `HumanMarkdownHandler` that appends `log.human()` calls (level 25, between INFO and WARNING) to daily markdown files at `logs/YYYY-MM-DD DayOfWeek.md`. Uses `O_APPEND` mode for concurrent-safe writes. This is the audit trail.

The `log.human()` method itself comes from `AuditLogger` in `assistant_sdk.logging`. All modules that need audit logging use `from assistant_sdk.logging import get_logger` instead of `logging.getLogger`. Both `main.py` and `worker.py` import `app.human_log` to ensure the file handler is registered.

### Shared Action Layer (`actions/` - partially re-exported)

Cross-cutting actions that can be triggered from any integration's automations. The evaluate phase partitions actions into platform-specific and shared types via `enqueue_actions()`.

- **`actions/__init__.py`**: Re-exports `is_script_action()`, `is_service_action()`, `resolve_inputs()`, and `enqueue_actions()` from `assistant_sdk.actions`. The partitioning logic (scripts, services, platform actions) lives in the SDK.
- **`actions/script.py`**: Script executor. Writes a bash preamble (with `log_human`/`log_info`/`log_warn` helpers) plus the user's shell code to a temp file, runs via `subprocess.run`, processes `\x1e`-delimited log records, captures output. The `handle()` function is the worker handler for `script.run` tasks.

Script actions become individual `script.run` queue tasks. Service actions become individual `service.{domain}.{service_name}` queue tasks. Platform-specific actions are bundled separately. Each type has independent failure tracking in `failed/`.

### Config (`config.py`)

- Loads `config.yaml` eagerly at import time (module-level)
- Custom `!secret` YAML constructor resolves keys from `secrets.yaml`
- Dynamic Pydantic models built from manifest config schemas at startup
- `BaseIntegrationConfig` for shared fields (type, name, schedule, llm) with a computed `id` property (`{type}.{name}`)
- `BasePlatformConfig` for per-platform fields (classifications, automations)
- Discriminated union on integration `type` field
- `get_integration(integration_id)` and `get_platform(integration_id, platform_name)` both take the composite ID
- Classification shorthand (`"human": "prompt text"`) is normalized to full `ClassificationConfig` via `model_validator`
- Automation `then` values are normalized from single string or dict to a list of typed action models (`SimpleAction`, `ScriptAction`, `ServiceAction`, `DictAction`)
- `ScriptConfig` model for user-defined shell scripts (`description`, `inputs`, `timeout`, `shell`, `output`, `on_output`, `reversible`)
- `scripts: dict[str, ScriptConfig]` in `AppConfig`
- `YoloAction` accepts `str | dict`, and `!yolo` works on both scalar and mapping YAML nodes
- Safety validation flags script actions as irreversible unless `ScriptConfig.reversible` is `True`
- `_validate_script_references()` warns about automation rules referencing undefined scripts (automations are not disabled -- the handler skips unknown scripts at runtime)
- `QueuePolicyConfig` with `defaults` (`TaskPolicyConfig`) and per-type `overrides` dict
- `TaskPolicyConfig`: `deduplicate_pending` (bool, default true) and optional `rate_limit` (`RateLimitConfig` with `max` int and `per` duration string)
- `queue_policies: QueuePolicyConfig = QueuePolicyConfig()` in `AppConfig` -- defaults mean no behavior change for existing configs

### Result Routes (`result_routes.py`)

Routes service handler return values to configured destinations. After a service handler returns data, the worker calls `route_results(result, task)` which reads `on_result` from the task payload and dispatches to route handlers. Currently supports one route type:

- **`note`** — saves result to NoteStore as markdown (frontmatter: service, integration, inputs, sources, timestamps; body: text content) and writes a human log breadcrumb pointing to the file. If the task payload contains a `human_log` string (resolved at enqueue time from config or manifest templates), that string is used in the log breadcrumb instead of the generic "result saved (N chars)" format.

Falls back to the `note` route for service tasks that lack explicit `on_result` config. Routing failures are logged but never propagate — the task already completed successfully, and the result is preserved in the task YAML regardless.

Designed for extensibility: new route types (e.g., `chat_reply` for the future threaded chat interface) are added by implementing a handler function and adding an `elif` branch to the dispatcher.

### Worker (`worker.py`)

Polling loop: dequeue, route to handler by task type string, capture the return value, mark complete or failed, then route results. The handler registry lives in `app/integrations/__init__.py`. After `register_all()`, the worker also registers `script.run` for the shared action layer (`app.actions.script.handle`) and service handlers discovered from integration manifests.

When a handler returns a non-None result (service handlers), the worker: (1) passes the result to `queue.complete()` which stores it in the completed task YAML as an audit record, and (2) calls `route_results()` to persist the output via configured routes. This ordering ensures a routing failure never marks a completed task as failed — the result is preserved in `done/` as the recovery point.

Integrations are discovered through three channels: the builtin `app/integrations/` directory, a user-configurable custom integrations directory, and Python entry points (`assistant.integrations` group). Entry-point discovery allows packages under `packages/` to register themselves without being copied into `app/integrations/`.

## Conventions

- Type hints throughout, mypy strict on the SDK, graduated for `app/`
- `get_logger(__name__)` from `assistant_sdk.logging` in every module (not `logging.getLogger`)
- Pydantic `BaseModel` for all config/data structures
- `log.human()` for audit-visible actions, `log.info()` for operational details
- Context managers for IMAP connections (`with Mailbox(...) as mb:`)
- Tasks enqueue downstream tasks directly (e.g., `email.inbox.check` enqueues `email.inbox.collect` for each new email)
- Task type namespace: `{domain}.{platform}.{handler}` (e.g., `github.pull_requests.classify`). Cross-cutting handlers use a flat namespace (e.g., `script.run`).
