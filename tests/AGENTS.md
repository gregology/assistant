# Testing

See `tests/AGENTS.yaml` for the core testing philosophy (reversibility tiers, decision boundary focus, safety test checklist).

## Safety Tests (`tests/safety/`)

### Property-Based Tests (`test_automation_invariants.py`)

Uses Hypothesis to generate all possible classification outputs (500 examples) and asserts:
- Only known actions are ever produced (no unknown action can appear)
- Action count is bounded (no runaway automation chains)
- Missing classification keys never trigger automations (safe default)

### Chaos Tests (`test_chaos.py`)

Injects faults at the classification level. The dangerous failure mode is the LLM being confidently wrong, not unavailable:
- All confidences maxed to 1.0
- All confidences at 0.0
- Booleans flipped
- Contradictory classifications
- Out-of-range values (5.0, -1.0)
- Wrong types (string for boolean, number for enum)
- Missing keys, empty results, None values
- Hypothesis fuzz with chaotic values (500 examples)

All assert: only allowed actions produced, no crashes.

### Provenance Tests (`test_provenance.py`)

Tests the provenance derivation (`resolve_provenance`) and safety validation (`_validate_automation_safety`):
- Deterministic conditions produce `rule` provenance
- LLM conditions produce `llm` provenance
- Mixed conditions produce `hybrid` provenance
- Irreversible actions from `llm`/`hybrid` provenance are blocked at startup
- `!yolo` overrides the safety block (including `!yolo` on mapping nodes for script actions)
- Only unsafe automations are disabled, safe ones stay active
- Script actions are irreversible by default, blocked from LLM provenance without `!yolo`
- Scripts with `reversible: true` are allowed from LLM provenance without `!yolo`

### Reference Validation Tests (`test_reference_validation.py`)

Tests that `_validate_script_references` and `_validate_service_references` warn about automations referencing nonexistent scripts or services. These produce warnings only (automations are NOT disabled), but the warnings are the only signal a user gets that their automation will silently do nothing at runtime.

- Undefined script name → warning containing the name and integration path
- Defined script name → no warning
- `!yolo`-wrapped undefined script → still warns (yolo overrides safety, not existence)
- Unknown service type or service name → warning
- Malformed service `call` format (wrong number of dots) → warning
- Valid known service → no warning
- Integrations without platforms → no crash

## Filesystem Snapshot Assertions

The filesystem is the database. Assert on the entire directory tree state, not individual files:

```python
def snapshot_tree(base: Path) -> dict:
    counts = {}
    for subdir in sorted(base.iterdir()):
        if subdir.is_dir():
            counts[subdir.name] = len(list(subdir.iterdir()))
    return {"counts": counts, "total": sum(counts.values())}
```

After a full task lifecycle (`enqueue -> dequeue -> complete`): pending is empty, active is empty, done has exactly one file, total is conserved. No task should exist in two directories at once.

## Stateful Testing (`test_queue.py`)

`QueueStateMachine` uses Hypothesis `RuleBasedStateMachine` to randomly interleave enqueue, dequeue, complete, and fail operations. Invariants checked after every step:
- Total task count is conserved
- No task ID appears in two directories simultaneously

## Fixtures (`conftest.py`)

- `queue_dir` - Isolated tmp directory with queue subdirs, monkeypatches `queue.BASE_DIR`
- `notes_dir` - Isolated tmp directory for NoteStore operations
- Config bootstrap: Creates a minimal `config.yaml` if missing (config loads eagerly at import time)

## Test Coverage

```bash
uv run pytest --cov=app --cov-report=term-missing -v   # Line coverage with uncovered lines
uv run pytest --cov=app --cov-report=html               # HTML report (open htmlcov/index.html)
```

Don't chase 100%. Coverage priority follows the same rule as test rigor: proportional to irreversibility. 95% coverage on `evaluate.py` and `actions.py` matters more than 95% on `scheduler.py`. When coverage reports show gaps, check the reversibility tier of the uncovered code before deciding whether to write tests for it.

Package-level coverage (run from repo root):

```bash
uv run pytest --cov=assistant_sdk --cov-report=term-missing packages/assistant-sdk/tests/
uv run pytest --cov=assistant_email --cov-report=term-missing packages/assistant-email/tests/
```

## Running Tests

```bash
uv run pytest -v           # All tests
uv run pytest tests/safety # Safety tests only
```

CI runs on GitHub Actions (`.github/workflows/test.yml`): checkout, setup uv, sync, pytest.

## Test Organization

```
tests/
  conftest.py                           # Fixtures
  test_actions.py                       # Shared action partitioning, input resolution, template rendering
  test_cli.py                           # CLI parser routing, setup wizard, doctor subcommands
  test_config.py                        # YoloAction tag parsing, ScriptConfig, QueuePolicyConfig models
  test_llm.py                           # LLM conversation, schema validation, structured output retry
  test_loader.py                        # Integration discovery, manifest parsing, dynamic model construction
  test_queue.py                         # Queue lifecycle + stateful property tests
  test_queue_policy.py                  # Dedup, rate limiting, fingerprint, policy resolution
  test_result_routes.py                 # Service result routing (note persistence, custom paths, fallbacks)
  test_scheduler.py                     # interval_to_cron conversion and range validation
  test_script_execution.py              # Script executor, output capture, input injection, timeout handling
  test_sdk.py                           # SDK public API imports, ServiceManifest defaults, runtime registration
  test_services.py                      # Service manifest parsing, handler registration, enqueue with on_result
  test_store.py                         # NoteStore CRUD + archive
  test_supervisor.py                    # Process supervisor lifecycle, sentinel file, restart endpoint
  test_ui_presenters.py                 # UI presenters: secret masking, classification/automation formatting
  test_ui_routes.py                     # Web UI route handlers, secret masking in rendered pages
  test_worker.py                        # Worker dispatch, lifecycle (happy/fail/routing), stale recovery
  test_yaml_rw.py                       # YAML config round-trip read/write, comment preservation
  safety/
    test_automation_invariants.py        # Property tests: all possible classifications
    test_chaos.py                        # Chaos tests: garbage LLM output
    test_provenance.py                   # Provenance derivation + safety validation
    test_reference_validation.py         # Script/service reference existence checks

packages/assistant-email/tests/
  test_act.py                            # Action execution, allowlist enforcement
  test_check.py                          # Window parsing, inbox fetch ordering, IMAP criteria
  test_classify.py                       # Condition matching, operators, schema building
  test_email_store.py                    # EmailStore message-ID sanitization, dedup across subdirs
  test_evaluate.py                       # Snapshot construction, resolver patterns, automation evaluation
  test_mail_parsing.py                   # Header parsing (auth, unsubscribe, dates, calendar)

packages/assistant-gemini/tests/
  test_client.py                         # GeminiClient two-pass flow (mocked)
  test_web_research.py                   # Service handler with full task dict, with/without output_schema

packages/assistant-github/tests/
  test_client.py                         # GitHub API client parsing, search dedup, PR status derivation
  test_entity_store.py                   # GitHubEntityStore: save, find, find_anywhere, move_to_synced
  test_evaluate.py                       # PR/Issue snapshot construction, resolver, automation evaluation
  test_issue_store.py                    # IssueStore save, field mappings, URL generation, defaults
  test_pr_store.py                       # PullRequestStore save, field mappings, URL generation

packages/assistant-sdk/tests/
  test_action_partitioning.py            # Action detection, input resolution, script/service partitioning
  test_classify.py                       # Schema building (confidence/boolean/enum), Jinja2 environment
  test_evaluate.py                       # Automation evaluation engine: conditions, operators, dedup
  test_manifest.py                       # Manifest dataclasses (Service, Platform, Integration)
  test_models.py                         # Pydantic config models: YoloAction, ClassificationConfig
  test_note_store.py                     # NoteStore CRUD, archive, directory creation
  test_provenance.py                     # Provenance resolution from condition keys (rule/llm/hybrid)
  test_runtime.py                        # Runtime registration pattern and RuntimeNotRegistered guards
  test_task.py                           # TaskPayload and TaskRecord TypedDict smoke tests
```

Package tests import from `assistant_sdk.*` directly and run without loading the app config singleton. Run them in isolation with `uv run pytest packages/assistant-email/tests/` etc.
