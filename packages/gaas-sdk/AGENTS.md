# gaas-sdk

The contracts layer. Models, evaluation engine, classification utilities, NoteStore, manifest dataclasses, provenance resolution, runtime registration, and shared action partitioning. Zero dependency on `app.*`.

Every integration imports from `gaas_sdk.*` instead of `app.*`. The app modules (`app/evaluate.py`, `app/classify.py`, `app/store.py`, `app/actions/__init__.py`) are thin re-export shims that forward to this package. Those shims exist for backwards compatibility during the transition.

## Modules

```
src/gaas_sdk/
  models.py       # Pydantic config models (YoloAction, AutomationConfig, ClassificationConfig, etc.)
  evaluate.py     # Automation evaluation engine (_evaluate_automations, condition matching)
  classify.py     # build_schema(), make_jinja_env()
  store.py        # NoteStore (markdown + YAML frontmatter)
  manifest.py     # IntegrationManifest, PlatformManifest, ServiceManifest dataclasses
  provenance.py   # resolve_provenance() - determines rule/llm/hybrid from condition keys
  runtime.py      # Runtime registration - decouples integrations from app singletons
  actions.py      # enqueue_actions() - partitions scripts, services, platform actions
```

## Runtime registration

The tricky problem this solves: integration code needs to enqueue tasks, look up config, create LLM conversations. Previously that meant `from app.config import config` and `from app import queue` everywhere, coupling every handler to app internals.

Now integrations call `gaas_sdk.runtime.enqueue()`, `runtime.get_integration()`, etc. The app registers real implementations at startup via `runtime.register()` in `app/runtime_init.py`.

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
models.py, runtime.py, manifest.py, provenance.py  (no internal deps)
evaluate.py    -> models, provenance
classify.py    -> models
actions.py     -> runtime, evaluate
store.py       (only depends on python-frontmatter)
```

No circular imports. Models and runtime are foundational.

## Key design patterns

**MISSING sentinel** in `evaluate.py`: `MISSING = object()`. Used to distinguish "key not present" from `None`, `0`, `False`, or `""`. Missing keys cause automations to not fire (safe default).

**YoloAction wrapper** in `models.py`: wraps actions tagged `!yolo` in YAML. `isinstance(action, YoloAction)` is how the safety validation identifies explicitly acknowledged risks. Works on both scalar (`!yolo unsubscribe`) and mapping nodes (`!yolo {script: ...}`).

**Action partitioning** in `actions.py`: `enqueue_actions()` splits an action list into three buckets. Script actions become individual `script.run` queue tasks. Service actions become individual `service.{domain}.{service_name}` tasks with default `on_result: [{"type": "note"}]` routing. Platform-specific actions get bundled into a single platform act task. The partitioning is transparent to the rest of the pipeline. Service actions can override the default `on_result` via their config dict. Service actions also resolve a `human_log` Jinja2 template (config override > manifest default via `runtime.get_service_log_template()`) at enqueue time and store the rendered string in the task payload.

**Action deduplication** in `evaluate.py`: `evaluate_automations()` deduplicates string actions across matching rules. If two rules both produce `"archive"`, only the first occurrence is kept. Dict actions (service, script, draft_reply) and `YoloAction` wrappers are never deduplicated.

## When modifying this package

- This is the safety-critical code path. `evaluate.py` is where bugs become irreversible actions.
- Any change to condition matching or action partitioning needs corresponding safety test updates in `tests/safety/`.
- The SDK has no dependency on `app.*`. If you find yourself importing from `app`, you're going the wrong direction. Run `uv run lint-imports` to verify. The import-linter config in `pyproject.toml` enforces this boundary automatically.
- Re-export shims in `app/` must stay in sync. If you add a new public function here, add the re-export too. Run `uv run vulture app/evaluate.py app/classify.py app/store.py app/actions/__init__.py --min-confidence 80` to check for stale re-exports.
- Run `uv run mypy packages/gaas-sdk/src/ --ignore-missing-imports` after changing function signatures or adding new models. Type drift in the SDK propagates to every integration.
