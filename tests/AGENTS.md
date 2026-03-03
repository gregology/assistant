# Testing

Tests exist to enforce the Principle of Reversibility. The purpose of the test suite is not to reduce bugs generally. It is to guarantee that automated actions cannot cause irreversible harm.

## Core Rule: Test Rigor Proportional to Irreversibility

Every action must be categorized by reversibility tier before tests are written. The tier determines the testing strategy, not the code complexity.

| Tier | Examples | Testing Strategy |
|------|----------|-----------------|
| **Read-only** | Checking mailbox, classifying content, reading files | Standard unit tests |
| **Soft reversible** | Archiving a message, creating a draft | Filesystem snapshot assertions |
| **Hard reversible** | Marking as spam (may train server filters) | Shadow/dry-run verification |
| **Irreversible** | Unsubscribing, sending data externally | Property-based safety invariants, mandatory dry run |

## Test the Decision Boundary, Not the LLM

The LLM is non-deterministic. Asserting on its output is meaningless. The automation dispatch layer (`_evaluate_automations`, `_check_condition`, `_conditions_match`) is deterministic and is where bugs become irreversible actions. Tests focus here.

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

## Running Tests

```bash
uv run pytest -v           # All tests
uv run pytest tests/safety # Safety tests only
```

CI runs on GitHub Actions (`.github/workflows/test.yml`): checkout, setup uv, sync, pytest.

## When Adding New Actions

1. Categorize the action's reversibility tier
2. For platform-specific actions: add to `SIMPLE_ACTIONS` or dict action handling in `act.py`. For cross-cutting actions (like scripts): add to `gaas_sdk/actions.py`. For services: declare in the integration's `manifest.yaml`
3. Write tests matching the tier's strategy (see table above)
4. Add property-based tests in `test_automation_invariants.py` covering the new action
5. Add chaos tests in `test_chaos.py` for the new action under garbage inputs
6. Update the `ALLOWED_ACTIONS` set in both safety test files

## Test Organization

```
tests/
  conftest.py                           # Fixtures
  test_actions.py                       # Shared action partitioning, input resolution
  test_config.py                        # YoloAction tag handling, ScriptConfig
  test_queue.py                         # Queue lifecycle + stateful property tests
  test_llm.py                           # LLM conversation, schema validation
  test_loader.py                        # Manifest parsing, discovery, dynamic models
  test_result_routes.py                 # Service result routing (note persistence, custom paths, fallbacks)
  test_scheduler.py                     # interval_to_cron conversion
  test_script_execution.py              # Script executor, preamble logging, output capture
  test_services.py                      # Service manifest parsing, enqueue with on_result
  test_store.py                         # NoteStore CRUD + archive
  safety/
    test_automation_invariants.py        # Property tests: all possible classifications
    test_chaos.py                        # Chaos tests: garbage LLM output
    test_provenance.py                   # Provenance derivation + safety validation

packages/gaas-email/tests/
  test_classify.py                       # Condition matching, operators, schema building
  test_act.py                            # Action execution, allowlist enforcement
  test_email_store.py                    # EmailStore CRUD, move, dedup
  test_mail_parsing.py                   # Header parsing (auth, unsubscribe, dates, calendar)

packages/gaas-gemini/tests/
  test_client.py                         # GeminiClient two-pass flow (mocked)
  test_web_research.py                   # Service handler with full task dict, with/without output_schema
```

Package tests import from `gaas_sdk.*` directly and run without loading the app config singleton. Run them in isolation with `uv run pytest packages/gaas-email/tests/` etc.
