# assistant-sdk

The contracts layer. Models, evaluation engine, classification utilities, NoteStore, manifest dataclasses, provenance resolution, runtime registration, and shared action partitioning.

## Modules

```
src/assistant_sdk/
  models.py       # Pydantic config models (YoloAction, action types, AutomationConfig, etc.)
  evaluate.py     # Automation evaluation engine (evaluate_automations, condition matching)
  classify.py     # build_schema(), make_jinja_env()
  store.py        # NoteStore (markdown + YAML frontmatter)
  manifest.py     # IntegrationManifest, PlatformManifest, ServiceManifest dataclasses
  provenance.py   # resolve_provenance() - determines rule/llm/hybrid from condition keys
  runtime.py      # Runtime registration - decouples integrations from app singletons
  actions.py      # enqueue_actions() - partitions scripts, services, platform actions
  protocols.py    # Typing protocols (TaskHandler, ResolveValue, EnqueueFn)
  logging.py      # AuditLogger wrapper with typed log.human() support
```

## Runtime registration

The tricky problem this solves: integration code needs to enqueue tasks, look up config, create LLM conversations. Previously that meant `from app.config import config` and `from app import queue` everywhere, coupling every handler to app internals.

Now integrations call `assistant_sdk.runtime.enqueue()`, `runtime.get_integration()`, etc. The app registers real implementations at startup via `runtime.register()` in `app/runtime_init.py`.

Available runtime functions:

- `enqueue(payload, priority=5, provenance=None)` - queue a task (returns `str | None` -- `None` when rejected by policy)
- `get_integration(integration_id)` - look up integration config by composite ID
- `get_platform(integration_id, platform_name)` - look up platform config
- `create_llm_conversation(model, system)` - create an LLM conversation instance
- `get_llm_config(profile)` - get LLM backend config by profile name
- `get_notes_dir()` - get the notes directory path
- `set_service_log_template(task_type, template)` - store a human_log Jinja2 template for a service
- `get_service_log_template(task_type)` - retrieve a stored human_log template (returns None if absent)

Calling `enqueue` etc. before `register()` raises `RuntimeNotRegistered` with the function name. The service log template functions are simple key-value storage and work without `register()`.

## Module dependency order

```
models.py, runtime.py, manifest.py, provenance.py, logging.py  (no internal deps)
protocols.py   -> models (for type references)
evaluate.py    -> models, provenance, protocols
classify.py    -> models
actions.py     -> runtime, evaluate, protocols
store.py       (only depends on python-frontmatter)
```

No circular imports. Models, runtime, and logging are foundational.

## Key design patterns

**MISSING sentinel** in `evaluate.py`: `MISSING = object()`. Used to distinguish "key not present" from `None`, `0`, `False`, or `""`. Missing keys cause automations to not fire (safe default).

**YoloAction wrapper** in `models.py`: wraps actions tagged `!yolo` in YAML. `isinstance(action, YoloAction)` is how the safety validation identifies explicitly acknowledged risks. Works on both scalar (`!yolo unsubscribe`) and mapping nodes (`!yolo {script: ...}`).

**Typed action models** in `models.py`: actions flowing through the automation pipeline are Pydantic models, not raw strings and dicts. `SimpleAction` wraps platform action strings like `"archive"`. `ScriptAction` and `ServiceAction` wrap their respective dict shapes. `DictAction` is the catch-all for platform-specific dict actions like `{"draft_reply": "..."}`. Raw YAML values are normalized into these models at config parse time via `_normalize_action()` on `AutomationConfig`'s `model_validator`. When actions reach the queue, `_action_to_dict()` converts them back to raw values for task payloads, so downstream `act.py` handlers don't need changes.

**Action partitioning** in `actions.py`: `enqueue_actions()` splits an action list into three buckets. Script actions become individual `script.run` queue tasks. Service actions become individual `service.{domain}.{service_name}` tasks with default `on_result: [{"type": "note"}]` routing. Platform-specific actions get bundled into a single platform act task. The partitioning is transparent to the rest of the pipeline. Service actions can override the default `on_result` via their config dict. Service actions also resolve a `human_log` Jinja2 template (config override > manifest default via `runtime.get_service_log_template()`) at enqueue time and store the rendered string in the task payload.

**Action deduplication** in `evaluate.py`: `evaluate_automations()` deduplicates `SimpleAction` instances across matching rules. If two rules both produce `SimpleAction(action="archive")`, only the first occurrence is kept. Other action types (`ScriptAction`, `ServiceAction`, `DictAction`) and `YoloAction` wrappers are never deduplicated.

**AuditLogger** in `logging.py`: a thin wrapper around `logging.Logger` that adds a typed `.human()` method at level 25. Lives in the SDK so integration packages can import it without depending on `app.*`. The `HumanMarkdownHandler` that writes to daily markdown files stays in `app/human_log.py`. Use `from assistant_sdk.logging import get_logger` instead of `logging.getLogger`.

## When modifying this package

- Run `uv run mypy packages/assistant-sdk/src/ --ignore-missing-imports` after changing function signatures or adding new models. Type drift in the SDK propagates to every integration.
