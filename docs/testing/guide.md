# Testing Guide

This is the practical companion to the [testing philosophy](philosophy.md). It covers where tests live, what fixtures are available, and how to add tests when you introduce new actions.

## Running tests

```bash
uv run pytest -v                           # All tests (core + packages)
uv run pytest tests/safety                 # Safety tests only
uv run pytest packages/gaas-email/tests/   # Email tests in isolation (no app config)
uv run pytest packages/gaas-gemini/tests/  # Gemini tests in isolation
```

CI runs on GitHub Actions (`.github/workflows/test.yml`): checkout, setup uv, sync, pytest.

## Test organization

```
tests/
  conftest.py                           # Shared fixtures
  test_actions.py                       # Shared action partitioning, input resolution
  test_config.py                        # YoloAction tag handling, ScriptConfig
  test_queue.py                         # Queue lifecycle + stateful property tests
  test_llm.py                           # LLM conversation, schema validation
  test_loader.py                        # Manifest parsing, discovery, dynamic models
  test_scheduler.py                     # interval_to_cron conversion
  test_script_execution.py              # Script executor, preamble logging, output capture
  test_store.py                         # NoteStore CRUD + archive
  safety/
    test_automation_invariants.py        # Property tests: all possible classifications
    test_chaos.py                        # Chaos tests: garbage LLM output
    test_provenance.py                   # Provenance derivation + safety validation

packages/gaas-email/tests/
  test_classify.py                       # Condition matching, operators, schema building
  test_evaluate.py                       # Snapshot construction, resolver, automation evaluation
  test_act.py                            # Action execution, allowlist enforcement
  test_email_store.py                    # EmailStore CRUD, move, dedup
  test_mail_parsing.py                   # Header parsing (auth, unsubscribe, dates, calendar)

packages/gaas-gemini/tests/
  test_client.py                         # GeminiClient two-pass flow (mocked)
  test_web_research.py                   # Service handler with/without output_schema
```

Package tests import from `gaas_sdk.*` directly, not through `app.*` re-export shims. They run without loading the app config singleton, which means they can be executed in isolation.

## Fixtures

Defined in `tests/conftest.py`:

- **`queue_dir`** - Creates an isolated temp directory with queue subdirectories (`pending/`, `active/`, `done/`, `failed/`) and monkeypatches `queue.BASE_DIR` to point at it. Each test gets a clean queue.
- **`notes_dir`** - Isolated temp directory for NoteStore operations.
- **Config bootstrap** - If `config.yaml` doesn't exist when tests run, `conftest.py` creates a minimal one automatically. This is needed because config loads eagerly at import time.

## Safety tests

### Property-based tests (`test_automation_invariants.py`)

Uses Hypothesis to generate 500 random classification outputs per test and asserts:

- Only known actions appear in the output (the `ALLOWED_ACTIONS` set)
- The number of actions produced is bounded
- Missing classification keys never trigger automations (safe default behavior)

### Chaos tests (`test_chaos.py`)

Injects specific fault patterns at the classification level and asserts that safety boundaries still hold. Each chaos scenario produces only allowed actions with no crashes. See the [testing philosophy](philosophy.md) for the full list of fault patterns.

### Provenance tests (`test_provenance.py`)

Tests the provenance derivation system (`resolve_provenance`) and the startup safety validation (`_validate_automation_safety`). Verifies that irreversible actions from non-deterministic provenance are blocked unless `!yolo` is set. Covers script actions: scripts from LLM provenance are blocked by default, allowed with `!yolo` or when the script has `reversible: true`. Operates at the platform level, matching how automations are configured per-platform in the config.

## Stateful queue testing (`test_queue.py`)

`QueueStateMachine` uses Hypothesis `RuleBasedStateMachine` to randomly interleave enqueue, dequeue, complete, and fail operations. After every step, two invariants are checked:

- Total task count is conserved (tasks don't appear or disappear)
- No task ID appears in two directories simultaneously

## Filesystem snapshot pattern

The standard way to assert on queue state after a lifecycle:

```python
def snapshot_tree(base: Path) -> dict:
    counts = {}
    for subdir in sorted(base.iterdir()):
        if subdir.is_dir():
            counts[subdir.name] = len(list(subdir.iterdir()))
    return {"counts": counts, "total": sum(counts.values())}
```

Assert on the whole tree, not individual files. After `enqueue -> dequeue -> complete`: pending is empty, active is empty, done has one file, total is conserved.

## Checklist: adding a new action

1. **Categorize the reversibility tier.** Read-only, soft reversible, hard reversible, or irreversible. This comes first. See the [safety model](../architecture/safety-model.md) for tier definitions.

2. **Add the action.** For platform-specific actions: add to `SIMPLE_ACTIONS` or dict action handling in `act.py`. For cross-cutting actions (like scripts): add to the shared action layer in `gaas_sdk/actions.py`. For services: declare in the integration's `manifest.yaml` under `services:`. The `SIMPLE_ACTIONS` set must not grow without deliberate reversibility review.

3. **Write tests matching the tier.** Read-only gets unit tests. Soft reversible gets filesystem snapshot assertions. Hard reversible gets shadow/dry-run verification. Irreversible gets property-based safety invariants and mandatory dry run.

4. **Add property-based coverage.** Update `test_automation_invariants.py` so the Hypothesis-generated classifications can produce your new action. Add it to the `ALLOWED_ACTIONS` set.

5. **Add chaos coverage.** Update `test_chaos.py` to verify your action behaves correctly under garbage classification inputs. Add it to the `ALLOWED_ACTIONS` set there too.

6. **Log it.** Use `log.human()` when the action fires so it appears in the [daily audit log](../architecture/human-log.md).
