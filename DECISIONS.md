# Decisions Log

This document records the _why_ behind Assistant's architecture. Every entry is a decision that was made deliberately. If you're refactoring and find yourself about to undo one of these, read the rationale first.

Decisions are grouped by area. Within each area they're roughly chronological but that's not strict.

---

## Core Philosophy

### Sender-benefit vs receiver-benefit framing

Most inbox traffic exists to serve the sender, not the receiver. A terms of service update email exists because legal needs a paper trail. It was not written for you. Assistant automates triage to get sender-benefit traffic out of the way. The longer-term goal is transforming sender-benefit messages into receiver-benefit information (e.g. diffing a ToS update to surface what actually changed).

This framing drives feature prioritization. If a feature doesn't help the user reclaim attention, it doesn't belong.

### Three principles are structural, not aspirational

Reversibility, audibility, and accountability are enforced in code. They're not guidelines. Reversibility is enforced by the provenance system blocking irreversible actions from non-deterministic sources at config load time, with a defense-in-depth runtime check in each platform's `act.py`. Audibility is enforced by `log.human()` writing to daily markdown files. Accountability is enforced by the separation between LLM classification and deterministic dispatch.

If a principle can't be enforced in code, it needs to be redesigned until it can.

### The LLM classifies, deterministic code decides

This separation runs through every layer. The LLM produces classification results (confidence scores, booleans, enums). A pure Python function evaluates `when`/`then` rules against those results and produces a list of actions. The LLM never sees the action space. The dispatch function has no network I/O, no randomness, no LLM calls.

Why: the dispatch layer is where bugs become irreversible actions. Making it deterministic makes it testable. Making it testable means we can use property-based testing to assert that safety holds for _all_ possible inputs, not just the examples we thought of.

---

## Two-Process Architecture

### Server + worker, filesystem coordination

The FastAPI server handles the API and cron scheduling. A separate worker process polls the task queue and executes handlers. They share nothing in memory. The filesystem is the coordination layer.

Why: LLM inference is the memory-intensive operation. Separating it into the worker keeps the server lightweight. The worker can be restarted without losing queue state (it's on disk). No shared memory means no locking complexity.

The tradeoff is that you need two terminal windows to run the system. That's fine. The simplicity gained is worth the minor operational cost.

---

## Filesystem as Database

### YAML task files instead of Redis

Tasks are YAML files that move between `pending/`, `active/`, `done/`, `failed/` directories. There is no Redis, no SQLite, no message broker.

Two reasons. First, human inspectability. `ls data/queue/pending/` shows exactly what's waiting. `cat` any file to see the full task payload. No tooling required. Second, RAM is better spent on LLM inference than a queueing system.

The tradeoff is that the queue can't handle thousands of concurrent tasks efficiently. That's fine. Assistant processes dozens of emails and PRs per run, not millions.

### Markdown with YAML frontmatter for all persistent state

Every piece of state (emails, PRs, issues, classification results) lives in markdown files with YAML frontmatter. You can open any file in a text editor and see exactly what Assistant knows about that item.

This was chosen over a database because the data is inherently document-shaped and the access patterns are simple (read by key, list directory, move between directories). The human readability is the primary benefit. The secondary benefit is that notes integrate with tools like Obsidian without any export step.

---

## Task Queue Design

### Priority encoded in filename

Task IDs follow the format `{priority}_{timestamp}_{uuid}`. `sorted()` on filenames returns tasks in priority-then-time order. The queue is implemented as a sorted directory listing with no additional data structure.

Why: it means `ls pending/ | head` shows you the next task. No parsing needed for human inspection.

### Atomic dequeue via `os.rename()`

`os.rename()` is atomic on POSIX. If two workers race for the same file, one gets `FileNotFoundError` and returns `None`. No locks, no transactions.

Why: correctness without complexity. The concurrent dequeue simulation test verifies this actually works. This pattern won't work on NFS or across machines, but Assistant runs on a single host.

### Task conservation invariant

A task must exist in exactly one of the four directories at all times. This invariant is enforced by tests using Hypothesis `RuleBasedStateMachine` that randomly interleaves queue operations and checks conservation after every step.

Why: the dangerous failure mode is a task existing in two directories (processed twice) or zero directories (silently lost). Stateful property testing catches orderings that hand-written tests miss.

### Priority levels

| Priority | Purpose |
|----------|---------|
| 3 | Discovery/collection (get data quickly) |
| 5 | Default |
| 6 | Classification (process after collection) |
| 7 | Actions (execute after classification) |
| 9 | Low-confidence items |

Priority 9 for unauthenticated emails ensures the user's important messages are processed first. The gap between priority levels leaves room for future insertions and we can also use multiple numbers like 8, 801, 81... etc if we want finer granularity.

### TypedDicts for task payloads and queue records

`TaskPayload` and `TaskRecord` in `assistant_sdk.task` define the canonical shape of task dicts. They're structural (TypedDict), not runtime-enforced. mypy and IDEs can catch key typos; handler signatures document what they receive.

The base `TaskPayload` only covers fields shared across all task types: `type` (required) and `integration` (not required — `script.run` tasks omit it). Per-task-type fields (`uid`, `org`, `repo`, `inputs`, `on_result`, etc.) are accessed via `.get()` and intentionally not declared. Adding per-type subtypes is optional and can happen incrementally without breaking the base contract.

This was chosen over two alternatives:

- **Pydantic models** — Runtime validation would add overhead to every enqueue/dequeue cycle for no safety gain (the queue is internal, not a trust boundary). TypedDicts are zero-cost at runtime.
- **Per-type dataclasses** — Would require deserialization at every handler boundary. The dict-in, dict-out pattern is simpler and already established.

### Complete before route: task state is the source of truth

The worker completes a task (`queue.complete()`) before routing its results (`route_results()`). This means a handler failure marks the task as failed with no side effects, and a routing failure leaves the task completed with its result preserved in `done/`.

The alternative — routing before completing — creates partial-state risk: if routing partially succeeds (e.g., writes a note to disk) but then the completion step fails, the task is marked failed despite having already produced side effects. That violates task conservation's spirit: the task's state no longer reflects reality.

With complete-then-route, the `done/` task file (which includes the full result dict) is the recovery point. If routing fails, the result is never lost — it's in the completed task YAML and can be re-routed. Routing failures are logged but don't change the task's terminal state.

---

## Queue Policy System

### Policy layer above raw enqueue

`queue_policy.policy_enqueue()` wraps `queue.enqueue()` with config-driven dedup and rate limiting. The raw `enqueue()` still exists and is used directly by manual API triggers (`POST /integrations/.../run`). Scheduled tasks and `runtime.enqueue()` (automation-driven code) go through `policy_enqueue()`.

Why two entry points: if a user manually triggers a run via the API, they want it to happen now regardless of whether a similar task is pending or the rate limit has been hit. Policies exist to prevent runaway automation, not to block intentional human actions.

The risk of having two entry points is that a contributor might call `queue.enqueue()` directly in new automation code, bypassing policies. The naming convention makes the intent clear: `policy_enqueue` is the default for automated paths, raw `enqueue` is the exception for manual triggers.

### Policy inheritance via `model_fields_set`

Per-task-type overrides merge with defaults using Pydantic's `model_fields_set` to distinguish explicitly-set values from Pydantic-filled defaults. When a user writes `overrides: { "email.inbox.check": { rate_limit: { max: 5, per: "1h" } } }`, only `rate_limit` is in `model_fields_set`. The override inherits `deduplicate_pending` from defaults rather than clobbering it with the model's default value.

Why: naive `dict.update()` or `model.model_dump()` merging treats Pydantic defaults as user intent. If the default for `deduplicate_pending` is `true` and the user only wants to set a rate limit, a naive merge would still "set" `deduplicate_pending=true` — which happens to be correct by coincidence. But if the global default were later changed to `false`, the override would silently keep the old behavior. `model_fields_set` makes the merge correct regardless of what defaults are.

### Zero-YAML-parsing policy checks

Both dedup and rate limiting operate on filenames only, never parsing YAML. Dedup globs for `*--{fingerprint}--{task_type}.yaml` in `pending/`. Rate limiting globs for `*--*--{task_type}.yaml` across all directories and filters by timestamp extracted from the filename.

This is why the task ID format includes `--`-separated fingerprint and task type suffixes. The format was designed to support policy checks without the I/O cost of reading and parsing every task file. For a queue with dozens of pending tasks, this is the difference between a glob and dozens of YAML loads.

### Early-stop optimization in `count_recent()`

`count_recent()` sorts files by their embedded timestamp (descending) and stops scanning a directory once a file's timestamp falls before the cutoff. This avoids parsing filenames for tasks that can't possibly be within the rate limit window.

Why a separate optimization from pruning: even with pruning, a directory can accumulate files between prune runs. The early-stop makes `count_recent()` fast regardless of whether pruning has run recently. The two mechanisms are independent — either one helps on its own, and together they address both the scan cost and disk growth.

### Scheduled pruning of done/failed, not at startup

A daily cron job deletes task files in `done/` and `failed/` older than a configurable `retention` period (default 7 days). Pruning runs as a scheduler job, not at startup.

Why not at startup: `recover_stale_active()` runs at startup because stale active tasks represent an inconsistent state that must be resolved before the worker processes new tasks. Pruning has no such urgency — old completed tasks don't affect correctness. Running it daily via the scheduler is simpler and avoids adding latency to startup.

Why only `done/` and `failed/`: these are terminal states. Pruning `pending/` or `active/` would silently discard work. The task conservation invariant (enforced by stateful property tests) accounts for pruning as a valid transition that decrements the expected total.

---

## Result Routing

### Route results after completion, not before

The worker completes a task before routing its results. See "Complete before route: task state is the source of truth" in the Task Queue Design section for the full rationale. This ordering means the `done/` task file (with the full result dict) is always the recovery point if routing fails.

### `on_result` in task payload, not in config

Result routing is configured per-task via an `on_result` field in the task payload, not per-integration in `config.yaml`. Service actions set `on_result` at enqueue time based on the manifest and automation config.

Why: the same service might be called from different automations with different routing needs. One automation might want results saved as notes, another might want a webhook notification (future). Putting routing in the task payload rather than global config keeps this flexible without adding conditional logic to the router.

### Default-to-note for service tasks

Service tasks that lack explicit `on_result` config fall back to the `note` route. Non-service tasks (platform handlers like `email.inbox.classify`) with no `on_result` produce no routing — their results are platform-internal.

Why: service handlers exist to produce output (research results, summaries, etc.). Silently discarding that output is always wrong. The `note` fallback ensures service output is persisted even if the user hasn't configured explicit routing. Platform handlers, by contrast, drive pipelines (check → collect → classify → evaluate → act) where the "result" is the next task in the chain, not user-facing output.

### Audit metadata wins over result data in note frontmatter

When `_route_note` builds frontmatter, service result keys are merged first, then audit metadata (`service`, `integration`, `inputs`, `completed_at`) is set unconditionally. If a service handler returns a key like `"service"`, the authoritative metadata overwrites it.

Why: the audit trail must reflect where the result actually came from, not what the service handler claims. Allowing result data to shadow metadata would silently break provenance. The ordering is tested explicitly in `test_audit_metadata_not_overwritten_by_result`.

### Extensibility via route type dispatch

New route types (e.g., `chat_reply`, `webhook`) are added by implementing a handler function and adding a branch to the dispatcher in `route_results()`. Each route type is independent — a single task can have multiple routes, and a failure in one doesn't affect others.

The current implementation only has `note`. The dispatch pattern was chosen over a registry or plugin system because the number of route types will remain small (likely under 5) and a simple `if/elif` is easier to audit than dynamic dispatch for a system where routing failures should be obvious, not hidden behind abstraction layers.

---

## Provenance System

### Namespaces as provenance

The access path in a `when` condition IS the provenance. `classification.human` is always LLM output. `authentication.dkim_pass` is always deterministic. No annotation needed.

This was chosen over four alternatives we researched:

- **Provenance envelope on tasks** - Assumes provenance is per-task, but a single automation rule can mix deterministic and non-deterministic conditions. Per-task loses the granularity.
- **Per-field provenance metadata** - Requires annotating every value with its source. Bookkeeping that someone has to remember to do. The namespace approach makes provenance structural and impossible to forget.
- **HA-style trigger IDs** - Bundles too much new functionality (rule-based classification) with provenance tracking. Can revisit later.
- **Sidecar files** - Two files per entity. Orphan risk. Mixed file types in directories.

The namespace approach means provenance is a property of the data's location, not metadata attached after the fact. Every value has a namespace. The system cannot produce an unprovenanced value.

### Non-deterministic component dominates hybrid provenance

When an automation has both deterministic conditions (`domain: work.com`) and non-deterministic conditions (`classification.human: "> 0.8"`), the provenance is `hybrid` and it's treated with the same restrictions as `llm`. The non-deterministic component dominates the safety posture.

The reasoning: if any part of the decision was made by a non-deterministic system, the whole decision inherits that uncertainty. One trustworthy condition doesn't make an untrustworthy condition more trustworthy.

### Safety validation at config load time as the primary gate

Irreversible actions with `llm` or `hybrid` provenance are stripped from the automation list when the config loads. The system cannot be talked into running them later. A startup warning is logged explaining what was disabled and why.

Why: config-time stripping is the strongest guarantee. By removing unsafe automations before the server starts, the worker physically cannot encounter them in normal operation.

### Runtime provenance check as defense-in-depth

Each platform's `act.py` also checks provenance before executing irreversible actions. If an action is in `IRREVERSIBLE_ACTIONS` and provenance is `llm` or `hybrid`, it's skipped with a warning log — unless the action carries the `!yolo` marker (preserved as `{"!yolo": action}` in the task payload).

Why: config-time validation is the primary gate, but it's not the only code path. The manual trigger endpoints (`POST /integrations/{id}/run`) call `queue.enqueue()` directly, bypassing `policy_enqueue()` and config-time validation. If a handler constructs a task with irreversible actions from a non-config code path, the runtime check catches it. This follows the zero-trust principle: the dispatch layer should not assume its inputs were pre-validated.

### Reference validation warns but does not disable

`_validate_script_references` and `_validate_service_references` warn about automations that reference nonexistent scripts or services. They do not disable the automations (unlike `_validate_automation_safety`, which strips unsafe automations entirely). Malformed service call formats (wrong number of dot-separated parts) also produce a warning rather than being silently skipped.

Why: these are existence checks, not safety checks. A typo in a script name (`reserach_tos` instead of `research_tos`) is a misconfiguration bug, not a safety violation. The automation will harmlessly no-op at runtime. But the warning is the only signal the user gets — without it, they think they have automation coverage when they don't. `!yolo` does not suppress these warnings because yolo overrides provenance safety, not existence validation.

### `classified_by` separate from `classification`

LLM metadata (model, profile, timestamp) lives in `classified_by`, not inside `classification`. The dispatch layer never reads `classified_by`. The audit log and human review tools do.

Why: separation of concerns. Classification values are inputs to the dispatch function. LLM metadata is audit information. Mixing them means the dispatch function has to know which keys to ignore.

---

## The `!yolo` Override

### YAML tag, not a comment or a flag

`!yolo` is a custom YAML constructor that produces a typed `YoloAction` object. The safety validation checks `isinstance(action, YoloAction)` to skip the provenance block.

A YAML tag gives you type safety in the Python object model and visibility in both the config file and the runtime data structures. It's auditable at every layer: visible in the YAML, visible in the Pydantic model, logged at startup.

Every `!yolo`-tagged automation generates a warning at startup. The choice is deliberate and recorded.

---

## Safety Dispatch Layer

### `evaluate_automations` is a pure function

Takes classification results and automation rules. Returns a list of actions. No I/O, no LLM calls, no side effects. Given the same inputs, always returns the same outputs.

Why: this is where bugs become irreversible actions. Purity makes it exhaustively testable with Hypothesis.

### Missing keys fail safe

If a condition key refers to a classification field that doesn't exist in the result dict, the automation silently doesn't fire. A sentinel `_MISSING = object()` is used so that `None`, `0`, `False`, and `""` are all valid values distinct from "not present."

Why: the safe default is inaction. An automation that fires on missing data is worse than one that doesn't fire when it should have.

### None values fail safe, same as missing keys

`check_condition` returns `False` for any `None` value, regardless of classification type. The MISSING sentinel still distinguishes "key absent" from "key present with value None" at the resolver level, but at the condition-matching level both result in the automation not firing.

Why: a `None` confidence value (e.g. from a failed LLM parse) previously raised `TypeError` on `None >= 0.8`, which propagated out of `evaluate_automations` and aborted the entire automation loop. This meant a single broken classification field could prevent all automations from evaluating — including purely deterministic ones that had nothing to do with the broken field. The safe default is per-automation false, not global crash. The same guard applies to non-numeric values in confidence fields (e.g. a string `"low"` where a float was expected).

### Boolean conditions use `is`, not `==`

`value is condition` means a string `"yes"` returned by a confused LLM does not match `True`. Identity comparison, not equality.

Why: the chaos tests inject wrong types (string for boolean, number for enum). Identity comparison means type confusion from a broken LLM doesn't accidentally satisfy boolean conditions.

### AND semantics only, no OR

All conditions in a `when` block must match. If you need OR, write multiple automations.

Why: simplifies safety analysis. Each automation has a single, clearly defined provenance based on all its conditions. OR semantics would make provenance ambiguous (if one branch is deterministic and the other is LLM-based, what's the provenance?).

### All matching automations fire

Unlike some rule engines where the first match wins, Assistant fires every automation whose conditions are satisfied. Users design non-conflicting rule sets.

Why: first-match-wins creates implicit ordering dependencies that are hard to reason about. Fire-all means each automation is independent and can be understood in isolation. The tradeoff is that users need to avoid conflicting actions (e.g., `archive` and `trash` on the same email).

### Action allowlist

`SIMPLE_ACTIONS` is a frozen set in each platform's `const.py`. Unknown string actions are skipped with a warning, never executed. Dict actions (`draft_reply`, `move_to`) have their own explicit allowlist of keys.

Why: the allowlist is the last line of defense. Even if the config is misconfigured or a custom integration has a bug, the system cannot execute an action that isn't in the set. The set must not grow without a reversibility review.

---

## Prompt Injection Defense

### Dual-barrier defense

Two independent barriers. First: random salt markers in Jinja2 templates wrap untrusted content between `-----BEGIN UNTRUSTED {salt}-----` and `-----END UNTRUSTED {salt}-----`. The salt is `secrets.token_hex(4).upper()`, different every invocation. A `scrub` Jinja2 filter strips the closing delimiter from untrusted content to prevent delimiter injection.

Second: the deterministic dispatch layer. Even if injection succeeds completely and the LLM is fully manipulated, the worst outcome is a reversible action.

Why two barriers: the prompt barrier is probabilistic. It makes injection harder but can't prevent it. The dispatch barrier is deterministic. It limits the blast radius to reversible actions regardless of what the LLM does. Neither barrier is sufficient alone. Together they provide defense in depth.

---

## LLM Abstraction

### `ChatCompletionsBackend`, not `OpenAICompatibleBackend`

The concrete backend class is named after the API contract it speaks (`/v1/chat/completions`), not the company that invented the format. We considered `OpenAICompatibleBackend` (Vercel AI SDK pattern) and `OpenAILikeBackend` (LlamaIndex/LiteLLM pattern) but both embed "OpenAI" in a codebase that is explicitly backend-agnostic. Ollama, vLLM, llama.cpp, and others all implement this same endpoint independently. The format is bigger than the brand. `ChatCompletionsBackend` describes what the class does, ages well, and extends naturally to future backends (`EmbeddingsBackend`, etc.).

Originally named `LlamaCppBackend`, which was just wrong. It implied a specific runtime when the class works with any provider that speaks the format.

### Protocol-based backend, not an ABC

`LLMBackend` is a `@runtime_checkable` Protocol. Any object with a `chat()` method works. Test fakes don't need to inherit from anything.

Why: structural typing over nominal typing. Makes testing simpler and avoids the diamond inheritance problem if someone wanted to compose backends.

### One backend per LLM profile, not per conversation

`runtime_init.py` creates a single `ChatCompletionsBackend` per configured LLM profile at startup and captures them in the `create_llm_conversation` lambda closure. Every `LLMConversation` on the same profile shares the same `httpx.Client`.

Why: creating a new `httpx.Client` per conversation leaks socket file descriptors under sustained load (hundreds of classification tasks). Sharing the client fixes the leak and gets TCP connection reuse for free. The client is thread-safe and the backend has no mutable conversation state, so sharing is safe.

### Retry with conversation state cleanup

`_send_structured()` retries up to 3 times on schema validation failure. If all retries fail, it removes the dangling user message from the conversation history.

Why: without cleanup, a failed structured output attempt leaves the conversation in an inconsistent state. The user message is there but the assistant response is missing. Any subsequent message would have a confusing context.

### Named LLM profiles

Config defines profiles like `default` and `fast` with different backends/models. Integrations reference them by name.

Why: use a faster model for high-frequency tasks (email check every 30 minutes) and a more capable model for deeper analysis. One config change, no code changes.

### Two-level schema validation

The schema is passed to the API as `response_format` (for grammar-constrained generation) AND validated locally with `jsonschema.Draft202012Validator` afterward.

Why: not all backends honor `response_format`. Some ignore it. Local validation ensures correctness regardless of what the backend does. Draft 2020-12 was chosen deliberately over older drafts for better schema support.

---

## Note Store

### Platform-specific stores wrapping a generic NoteStore

`NoteStore` handles the generic read/write/move. `EmailStore`, `PullRequestStore`, and `IssueStore` add domain methods. The underlying storage is always markdown with frontmatter.

Why: the storage pattern is the same everywhere. Only the domain logic differs. Wrapping keeps the generic code generic and the domain code focused.

### `GitHubEntityStore` base class for PR and issue stores

`PullRequestStore` and `IssueStore` share identical logic for `find`, `find_anywhere`, `active_keys`, `update`, `move_to_synced`, and `restore_to_active` -- all keyed by `(org, repo, number)`. The `GitHubEntityStore` base class in `packages/assistant-github/src/assistant_github/entity_store.py` provides these methods. Each subclass overrides only `save()` with entity-specific field mappings.

Why: the two stores were 106 and 105 lines of nearly identical code. Divergence risk was high -- a bug fix in one might not propagate to the other. The base class lives at the integration level (not in the SDK) because it's GitHub-specific infrastructure, not a core pattern.

### `synced/` subdirectory

Active notes live in the root directory. Notes that no longer require attention live in `synced/`. For email, this means "no longer in the IMAP inbox." For GitHub, "PR merged or issue closed."

The store makes no attempt to mirror IMAP folder structure or GitHub states. It only knows "active" or "not active." This is deliberate. IMAP folder structures differ by provider and mirroring them would create fragile coupling.

### Email filename: `YYYY_MM_DD_HH_MM_SS__{sanitized_message_id}.md`

Timestamp prefix ensures chronological sort by filename. Sanitized Message-ID suffix enables deduplication lookup via `rglob`. Double underscore separates the two parts because single underscores appear in both timestamps and message IDs.

Emails without a Message-ID use `imap_{uid}` as the key. This is a fallback for malformed emails.

### GitHub filename: `{org}__{repo}__{number}.md`

Double underscore again because org names and repo names can contain single characters like hyphens. Human readable and collision-free.

### `inbox_message_ids()` vs `known_message_ids()`

`inbox_message_ids()` scans only the root directory. `known_message_ids()` scans the entire tree recursively. The check handler needs both: root-only to know what's active, recursive to avoid re-downloading emails that were already processed and moved to `synced/`.

---

## Plugin System

### Three-channel discovery model

Integrations are discovered through three channels: builtin directory (`app/integrations/`), custom directory (user-configured), and Python entry points (`assistant.integrations` group). Priority: builtin > custom > entry points.

The original two-directory model followed HA's pattern. Entry-point discovery was added when email and GitHub were extracted into installable packages under `packages/`. Entry points let packages register themselves without being copied into `app/integrations/`. The priority order means users can shadow an installed package with a local override during development, same as HA's `custom_components/` behavior.

### `manifest.yaml` for discovery, not Python conventions

Each integration declares its config schema, platforms, dependencies, and entry tasks in `manifest.yaml`. Python code is only loaded when the worker starts.

Why: the manifest can be read without importing the integration's Python code. This matters because integration code may have external dependencies (imap-tools, etc.) that might not be installed. The manifest tells us what's needed before we try to load anything.

### Dynamic Pydantic models from manifest schemas

Config schemas in `manifest.yaml` are JSON Schema. At startup, `build_integration_model()` constructs Pydantic models dynamically using `pydantic.create_model()`. The discriminated union on the `type` field means Pydantic picks the right model automatically.

Why: custom integrations can define their own config fields without modifying core code. The dynamic model approach means adding a new integration type is a YAML change, not a Python change.

### Integration isolation over shared abstractions

Each integration owns its pipeline stages. `evaluate.py`, `classify.py`, `act.py` -- each lives inside the integration package with platform-specific logic (snapshot construction, prompt rendering, action execution, value resolution).

However, the automation evaluation engine and classification schema builder are **infrastructure**, not pipeline logic. They operate on `AutomationConfig` and `ClassificationConfig` and have no integration-specific knowledge. They live in `assistant_sdk.evaluate` and `assistant_sdk.classify` respectively, in the same category as `resolve_provenance` and `YoloAction`.

The line: if it operates on core config types and is identical across all platforms (evaluation engine, schema building, provenance), it goes in the SDK. If it touches platform-specific data (snapshots, prompts, stores, actions, value resolution), it stays in the integration.

This was originally "everything stays in the integration" but was refined when three-way duplication of the evaluation engine across platforms created a maintenance burden. The evaluation engine is the safety-critical dispatch boundary -- having a single authoritative copy reduces the risk of divergence in safety-critical code. Integrations import from `assistant_sdk.evaluate` and `assistant_sdk.classify`.

### Pipeline handler duplication is accepted — extract when a fourth integration lands

The evaluate, classify, and check handlers across email/inbox, github/issues, and github/pull_requests are 80-93% structurally identical (snapshot construction, resolver closures, the evaluate-resolve-enqueue flow, the classify skip-check). This is known duplication, not accidental.

Why not extract now: three platforms is not enough signal that the pattern is stable. The email resolver's nested dict lookups (`authentication.*`, `calendar.*`) don't fit neatly into a generic resolver, which suggests the abstraction boundary isn't obvious yet. Premature extraction creates coupling between the SDK and platform handler structure, making it harder to write a handler that breaks the mold. The cost of the current duplication is manageable — it's ~300 lines per platform of well-tested, straightforward code.

When to revisit: when a fourth integration (e.g., Linear, Slack) is added. At that point there will be enough examples to see which parts of the orchestration flow are truly universal and which are platform-specific. The extraction should target the orchestration layer (load note, build snapshot, evaluate, resolve provenance, enqueue) while keeping snapshot definitions, resolver hooks, and prompt rendering in the integration. Start with evaluate handlers (highest duplication at 93%) as a pilot.

Until then, when fixing a bug in the evaluation flow, grep for the same pattern across all three platforms and apply the fix to each. This is the cost of isolation.

### Declined: extract generic handler helpers into SDK now (issue #117, 2026-03-16)

Issue #117 proposed extracting generic `evaluate_handler()` and `classify_handler()` helpers into the SDK immediately, reducing each platform's handler to ~15 lines of wiring. The proposal was well-structured (phased: tests first, then evaluate extraction, then classify extraction, then guide update) and correctly identified the duplication.

Declined because the decision above ("extract when a fourth integration lands") still applies. The trigger condition — a fourth integration — has not been met. The three reasons for deferral remain valid: the abstraction boundary is unclear (email's `authentication.*` and `calendar.*` resolver prefixes don't fit a generic pattern), premature extraction risks coupling the SDK to a handler shape that may not suit future integrations, and the current maintenance cost (~300 lines per platform, grep-and-fix workflow) is acceptable at three platforms.

When a fourth integration is added, this issue should be revisited as the starting point for the extraction design.

### `const.py` loaded via `spec_from_file_location`, not `import_module`

Platform const modules are loaded using `importlib.util.spec_from_file_location` for both builtin and custom modules. This bypasses the package `__init__.py`, avoiding circular imports when `const.py` is loaded during config validation (which happens at module import time, before the full integration packages are initialized).

Previously, builtin const modules used `importlib.import_module`, which traversed the package hierarchy and triggered `__init__.py` imports. This created a circular dependency: `config.py` → `_validate_automation_safety` → `load_platform_const_module` → package `__init__.py` → `evaluate.py` → `app.evaluate` → `app.config`.

### `const.py` loaded separately from the main module

Safety constants (`DETERMINISTIC_SOURCES`, `IRREVERSIBLE_ACTIONS`) are loaded at config validation time. The full integration module (which may have heavy imports or side effects) is only loaded when the worker starts.

Why: config validation happens in both the server and the worker. Loading the full module in the server (which only does scheduling) would pull in unnecessary dependencies. The lighter-weight `const.py` path avoids this.

### Missing `const.py` defaults to all-actions-irreversible (fail-safe)

When `load_platform_const()` returns `None` — because `const.py` doesn't exist, fails to import, or the manifest isn't found — the safety validation treats all SimpleAction/DictAction types as irreversible and all conditions as non-deterministic. A warning is logged. The net effect: any automation with conditions and actions is blocked from firing when platform safety constants are unavailable.

Four approaches were considered: (1) warning only, (2) fail-safe only, (3) config validation error, (4) warning + fail-safe. Approach 4 was chosen because it combines observability with enforcement. The warning tells custom integration authors what's missing. The fail-safe prevents accidental bypass of safety checks while they fix it.

This is consistent with the existing pattern: scripts default to irreversible, services default to irreversible, unknown action types default to irreversible. Missing `const.py` follows the same "guilty until proven innocent" approach.

### Custom integrations use `assistant_ext.{domain}` namespace

Custom integration packages are loaded into `assistant_ext.*` via `importlib.util.spec_from_file_location()`. A synthetic namespace package is created in `sys.modules`.

Why: avoids stdlib shadowing (a custom integration called `email` would shadow Python's `email` module) and cross-integration leakage. Relative imports within the custom integration still work because the package structure is preserved.

### Domain must match directory name

If `manifest.yaml` says `domain: email` but the directory is `email_v2/`, the manifest is rejected.

Why: the domain determines the handler namespace (`email.inbox.check`). If it doesn't match the directory, handler registration uses names that don't correspond to the filesystem layout. Confusion guaranteed.

### Dependencies checked, not auto-installed

`check_dependencies()` tries to import each declared dependency. If it fails, the integration is skipped with a warning.

Why: auto-installing packages at runtime is a side effect that can break environments. Assistant is explicit about what's installed. If you want an integration, install its deps with `uv add`.

---

## SDK Extraction and Package Architecture

### Shared SDK package instead of copying code

Integrations were tightly coupled to 7 `app.*` modules. Every handler file imported from `app.config`, `app.evaluate`, `app.classify`, `app.store`, `app.queue`, `app.llm`. That made them impossible to develop, test, or distribute independently.

The `assistant-sdk` package extracts the contracts layer: models, evaluation engine, classification utilities, NoteStore, manifest dataclasses, provenance resolution, runtime registration, and shared action partitioning. Integrations depend on `assistant-sdk` instead of `app.*`.

The alternative was each integration copying what it needs from `app.*`. No shared package. Simple, but then you have three copies of the evaluate engine drifting apart. Bug fixes need to land in multiple places. The SDK is small (~400 lines total across 8 modules) and the extraction boundary is clean, so the coordination cost of a shared package is low.

### Runtime registration instead of dependency injection or ABC contracts

Integration code needs to enqueue tasks, look up config, create LLM conversations. Previously that meant `from app.config import config` and `from app import queue`. The runtime registration pattern replaces this: integrations call `assistant_sdk.runtime.enqueue()`, `runtime.get_integration()`, etc. The app registers real implementations at startup.

Two alternatives were considered:

**Dependency injection via constructor args.** Every handler function would receive a `context` object with `enqueue`, `get_config`, etc. Clean in theory. In practice it means changing every handler signature, threading context through 5+ levels of the check->collect->classify->evaluate->act pipeline, and updating every test. The handler registry pattern (simple function that takes a task dict) is one of the better parts of the current design.

**Abstract base classes for integration contracts.** Define `class BaseIntegration(ABC)` with abstract methods. This works well for frameworks like Django but fights the current architecture. Assistant integrations are bags of handler functions registered by name, not class hierarchies. Forcing them into an OOP shape would mean rewriting the handler registry, the manifest system, and the worker dispatch.

### Installable packages over namespace packages

Email, GitHub, and Gemini ship as independent packages under `packages/` with their own `pyproject.toml` files. Each registers as a Python entry point.

The alternative was namespace packages (`assistant.sdk`, `assistant.email`, etc.) in a single package. This avoids multi-package complexity but has rough edges with editable installs, and doesn't give you independent installability. You still can't `pip install assistant-email` without pulling the whole repo. The point was making integrations distributable.

### Services as a manifest declaration

Integrations can declare callable services alongside (or instead of) platforms. A service is a handler invoked from automation `then` clauses. Services are irreversible by default, same as scripts.

The service system reuses all existing infrastructure: action partitioning, provenance gating, `!yolo` overrides. No new safety machinery was needed. Service actions go through `enqueue_actions()` just like script actions.

---

## Platforms Pattern

### Platforms within integrations, following HA

Each integration can have multiple platforms. GitHub has `pull_requests` and `issues`. Email has `inbox`. Platforms have their own config schemas, entry tasks, safety constants, and handler sets.

Why: the GitHub integration needs one IMAP connection (well, one `gh` auth) but two distinct resource types with different classification schemas and automation rules. Platforms handle this naturally. Config at the integration level is shared (orgs, repos, credentials). Config at the platform level is specific (classifications, automations).

### Three-level handler aggregation

Platform exports `HANDLERS = {"check": fn}`. Integration prefixes: `"pull_requests.check"`. Top-level prefixes: `"github.pull_requests.check"`. Globally unique task type strings without coordination.

Why: the naming convention produces human-readable task types. You can look at a task YAML and know exactly which handler processes it.

### Entry tasks per platform

Each platform has its own `entry_task`. The scheduler enqueues entry tasks for all enabled platforms within an integration.

Why: `github.pull_requests` and `github.issues` share an integration config block (same schedule, same orgs) but need to start their own pipelines independently.

---

## Configuration

### Eager module-level config loading

`config.py` creates the config singleton at import time. Misconfigured YAML fails at startup, not at first use.

Why: fail fast. If your config is broken, you find out immediately, not thirty minutes later when the first schedule fires.

### `!secret` YAML constructor

Credentials never live in `config.yaml`. A custom YAML loader resolves `!secret key` tags from `secrets.yaml`.

Directly borrowed from Home Assistant. Both files are gitignored. The separation means you can share your config structure without leaking credentials.

### Classification shorthand

`human: "is this a personal email?"` normalizes to `ClassificationConfig(prompt="...", type="confidence")`.

Why: confidence is the most common classification type. The shorthand reduces YAML verbosity for the common case while keeping a single internal representation.

### Composite IDs: `{type}.{name}`

`BaseIntegrationConfig.id` returns `email.personal` or `github.my_repos`. Computed property, never stored in YAML.

Follows HA's entity_id pattern. Allows multiple instances of the same integration type (two email accounts, two GitHub configs) with unique identifiers.

### Schedules per integration, applied per platform

One `schedule:` block at the integration level applies to all platforms. Platforms can't have independent schedules.

Why: keeps config simpler. If you need different schedules for PRs and issues, create two integration blocks. In practice, polling both on the same schedule is usually what you want.

---

## Evaluate as a Separate Pipeline Step

### classify -> evaluate -> act, not classify -> act

Classification stores results in the note's frontmatter. Evaluation reads from frontmatter, not from IMAP or the GitHub API.

The primary reason: reducing unnecessary external requests. The data needed for automation evaluation (classification results, email properties, authentication flags) is already saved locally. There's no reason to hold an IMAP connection open or make another API call just to evaluate `when`/`then` rules.

The structural benefit: the evaluate step is a pure function from frontmatter data to action list. It's independently testable without mocking any external service. All the safety-critical property tests and chaos tests target this step directly.

---

## Email Integration Specifics

### `gh` CLI as GitHub API client

GitHub API calls go through `subprocess.run(["gh", "api", ...])`. Not `httpx`, not PyGithub, not the REST API directly.

Deliberate tradeoff. The `gh` CLI handles authentication (OAuth device flow, SSH keys, token storage), rate limiting, and pagination. Using it means Assistant doesn't need to implement any of that. The cost is a hard dependency on `gh` being installed and authenticated, but anyone working with GitHub repositories almost certainly has it already.

### IMAP folder auto-discovery

`_discover_folders()` lists IMAP folders and matches special-use flags (`\Archive`, `\Drafts`, `\Junk`, `\Trash`). Folder names are not hardcoded.

Why: Gmail uses `[Gmail]/All Mail`. Others use `Archive`. Fastmail uses something else. Special-use flags are standardized. Folder names are not.

### `Received:` header for timestamps, not `Date:`

The `Date:` header is set by the sender and can be forged. The first `Received:` header is set by the server that accepted the email from the internet.

Why: more reliable timestamp. Falls back to `msg.date` if no `Received:` header exists.

### Authentication-based priority tiering

Emails where any of DKIM/DMARC/SPF fail get classified at priority 9 (last). Authenticated emails get priority 6.

Why: prioritizes emails that are likely more useful for the human as opposed to spoofed emails.

### RFC 8058 one-click unsubscribe only

`unsubscribe()` requires both `List-Unsubscribe` (with an HTTP URL) and `List-Unsubscribe-Post` headers. It uses the HTTP POST method per the RFC.

Why: `mailto:` unsubscribe links are unreliable and would require sending an email (an irreversible action that leaks information). HTTP POST with the standardized payload is the reliable path. Requiring both headers means we only unsubscribe when the sender properly supports it.

### Draft reply preserves threading headers

`In-Reply-To` is set to the original's `Message-ID`. `References` is built from the original's `References` chain plus `Message-ID`.

Why: without these headers, the draft shows up as a new conversation in the recipient's mail client instead of a reply in the existing thread.

### `tldextract` for root domain extraction

`root_domain` uses the `tldextract` library rather than naive string splitting on `.`. `tldextract` maintains the Mozilla Public Suffix List, which knows that `.co.uk` is a single suffix but `.com` is not.

Why: splitting on `.` and taking the last two parts works for `mail.company.com` → `company.com`, but fails for `mail.company.co.uk` → `co.uk` (wrong) instead of `company.co.uk` (correct). The Public Suffix List is the only reliable way to distinguish multi-part TLDs. The library caches the list on disk and updates it lazily.

### `now()` expressions in conditions

`calendar.end: "<now()"` lets you archive past calendar events. This is the one place where evaluation isn't purely a function of stored data.

Why: time-based rules are a natural fit for calendar events. Checking "has this event already happened?" requires comparing against the current time. The `now()` syntax keeps this readable in YAML.

---

## Human Log

### Custom log level 25

`log.human()` sits between INFO (20) and WARNING (30). A filter ensures only HUMAN-level messages hit the daily markdown file. `log.info()` for operational details stays in the normal log output.

Why: the human log answers "what did Assistant do today?" The operational log answers "why did the IMAP connection fail?" Different audiences, different files.

### `O_APPEND` for concurrent writes

The file handler uses `O_APPEND` mode. POSIX guarantees atomic appends up to PIPE_BUF (4096 bytes), well above any single log line.

Why: both the server and worker processes write to the same daily log file. `O_APPEND` makes this safe without file locking.

### Imported in both processes via `noqa: F401`

Both `main.py` and `worker.py` import `app.human_log` to register the handler. The import appears unused (hence `noqa`) but is needed for the side effect.

Why: the handler must be active in both processes. Without the import, whichever process doesn't import it silently drops human log entries.

---

## Testing

### Rigor proportional to irreversibility, not complexity

A one-line HTTP POST that unsubscribes deserves more testing than 100 lines of email parsing. The parsing can never trigger an irreversible action. The POST can.

Every action is categorized by reversibility tier before tests are written. The tier determines the testing strategy.

### Property-based testing over examples

"For all possible classifications, no unknown action is ever produced" is a stronger guarantee than "for this one test email, archive was produced." Hypothesis generates 500 examples per run.

Why: safety invariants should hold for all inputs. A developer writing example tests will think of the obvious cases. Hypothesis finds the edge cases the developer didn't think of.

### Chaos testing for confidently-wrong LLM output

The dangerous failure mode is the LLM being confidently wrong, not unavailable. Retry logic handles unavailability. Nothing handles a model that returns 1.0 confidence for everything with maximum conviction.

Chaos tests inject exactly this: maxed confidences, zeroed confidences, flipped booleans, contradictory classifications, out-of-range values, wrong types. The assertion is always the same: only allowed actions produced, no crashes.

### Filesystem snapshot assertions

Assert on the entire directory tree state, not individual files. After a lifecycle: pending empty, active empty, done has one file, total conserved.

Why: per-file assertions miss emergent problems like tasks stuck in intermediate states or duplicated across directories.

### Minimal config bootstrap in conftest.py

Config loads eagerly at import time. Tests need a valid `config.yaml` before any app module is imported. `conftest.py` creates a minimal one if missing.

Why: without this, running tests on a fresh clone fails because `config.yaml` is gitignored. The bootstrap creates the minimum viable config.

---

## Dependency Choices

### No database dependency

No SQLite, no Postgres, no Redis. The filesystem handles task queueing and data storage. See "Filesystem as Database" above.

### No LLM SDK

Uses the OpenAI-compatible `/v1/chat/completions` endpoint directly via `httpx`. No `openai` package, no `anthropic` package, no LLM-specific SDK.

Why: backend-agnostic by design. Ollama, llama.cpp, vLLM, and OpenAI all speak the same endpoint format. Adding an SDK would couple Assistant to a specific provider.

### `httpx` over `requests`

`httpx` is the HTTP client. Async-capable, already a FastAPI dependency, supports the same API surface as `requests`.

### `imap-tools` over raw `imaplib`

Higher-level IMAP abstraction. Handles encoding, folder listing, message parsing. Life is too short for raw IMAP protocol strings.

### `python-frontmatter` for note storage

Reads and writes YAML frontmatter in markdown files. Does one thing well.

### `hypothesis` for property-based testing

Generates random inputs for safety invariants. The `RuleBasedStateMachine` is particularly valuable for the queue conservation tests.

### `icalendar` for calendar parsing

Parses `.ics` attachments. Extracts method, sequence, attendees, start/end times. Standard library doesn't handle iCalendar.

### `fastapi-crons` for scheduling

Runs cron jobs inside the FastAPI process. Avoids a separate scheduler process or dependency on system cron. The `interval_to_cron()` helper converts friendly syntax (`every: 30m`) to cron expressions for this library.

---

## Shared Action Layer

### Scripts as a cross-cutting action type, not per-platform

Scripts can be triggered from any integration's automations (email, GitHub, etc.). Rather than adding script awareness to every platform's `act.py` or `evaluate.py`, there's a shared action layer in `app/actions/` where the evaluate phase partitions actions into platform-specific and shared actions.

Three alternatives were considered. Adding a `_handle_script` function to every platform's `act.py` would mean script logic duplicated across every platform. Intercepting scripts in each platform's `evaluate.py` before the queue would couple evaluation to script execution. A middleware layer between the queue and handlers would add a new processing stage to understand.

The chosen approach is cleaner: `enqueue_actions()` is called by each platform's evaluate handler. It splits the action list. Script actions become independent `script.run` queue tasks. Platform actions go to the platform's `act.py` as before. The evaluate handler doesn't need to know what a script does. The script handler doesn't need to know which platform triggered it.

### Scripts are irreversible by default

The system can't statically verify what shell code does. A script that `curl`s an external API is irreversible. A script that writes to a local file is probably reversible. Rather than guess, every script is treated as irreversible unless the author explicitly opts in with `reversible: true` on the script definition.

This means script actions from `llm` or `hybrid` provenance are blocked at config load time (like `unsubscribe`) unless wrapped in `!yolo` or the script is marked `reversible: true`.

### `!yolo` on YAML mappings

The original `!yolo` tag only worked on scalars (`!yolo unsubscribe`). Script actions are dicts, not strings. The YAML constructor was extended to handle mapping nodes:

```yaml
- !yolo
  script:
    name: research_tos
    inputs:
      domain: $domain
```

`YoloAction.value` became `str | dict`. `__hash__` uses `repr(self.value)` for stable hashing of both types. This keeps the existing safety validation infrastructure working without special cases.

### Input resolution at evaluate time, not execution time

Script inputs use `$field` references (e.g., `$domain`) that are resolved against the automation context. This resolution happens in the evaluate phase, not in the script executor. The executor only receives fully resolved string values.

Why: the evaluate phase has the snapshot context (email properties, classification results). The script executor runs later, potentially in a different worker process, and shouldn't need to reconstruct the snapshot. Resolving early also means the resolved values are visible in the queue task YAML, which helps debugging.

### Separate queue tasks per script

Each script action becomes its own `script.run` queue task. An automation that triggers two scripts and an archive produces three queue tasks: two `script.run` tasks and one platform act task.

Why: scripts can be long-running. A 5-minute ToS research script shouldn't block a 100ms archive. Independent tasks also mean independent failure tracking. A failed script lands in `failed/` with its error while the platform action still completes.

### Preamble-injected logging helpers

Every script gets a bash preamble prepended with `log_human`, `log_info`, and `log_warn` functions. These write `LEVEL\tMESSAGE` records to a temp file (`$ASSISTANT_LOG`) using `\x1e` (ASCII Record Separator) as the record delimiter.

Why `\x1e` instead of newlines: multi-line log messages (heredocs) need to pass through cleanly. The Record Separator character never appears in natural text. After the script completes, the executor reads the file, splits on `\x1e`, and routes each record to the appropriate Python logger.

### Config-only scripts, no `scripts/` directory

Scripts are defined inline in `config.yaml` under the `scripts:` section. There's no separate `scripts/` directory with `.sh` files.

Why: a web UI is the eventual goal for editing scripts. Keeping them in config means the config file is the single source of truth. Shell code in YAML is ugly, but it's also easily parseable and validatable by the config system. A future web UI will provide a better editing experience.

---

## Web UI

See `docs/architecture/web-ui.md` for the full research and architecture document.

### No built-in authentication

Assistant does not implement authentication or user management. The web UI is open by default.

Assistant is a personal assistant running on your own infrastructure. Adding auth creates maintenance burden, dependency surface area, and configuration complexity that's disproportionate to the threat model. A personal tool running on localhost doesn't need a user database.

Users who need access control should use infrastructure-level solutions: reverse proxy with basic auth (Caddy, nginx), VPN/tailnet (Tailscale, WireGuard), or firewall rules. This is the same model Home Assistant used before HASS.io, Grafana in local mode, and Prometheus.

Two safety measures exist regardless: the UI binds to `127.0.0.1` by default (not `0.0.0.0`) to prevent accidental network exposure, and `!secret` values are never displayed in plaintext. Mutating endpoints require explicit confirmation for destructive operations.

### YAML stays as source of truth, UI is a peer

The UI reads config from `config.yaml` and writes back to it. The YAML file is the source of truth. The UI is a convenience layer on top, not a replacement.

This was a deliberate rejection of Home Assistant's approach where Config Flow replaced YAML for most integrations. HA's ADR-0010 caused significant community friction: power users lost version control, bulk editing, and diffing. We looked at Grafana's model instead, where file-provisioned content is displayed in the UI without being "owned" by it.

The tradeoff: we needed to solve YAML round-tripping (preserving comments and formatting when the UI writes back). `ruamel.yaml` handles this. PyYAML strips comments. This was solved by building round-trip editing via `ruamel.yaml` (typ='rt') in `app/ui/yaml_rw.py`.

### Incremental delivery: viewer first, then editing

The UI was built incrementally: config viewer first, then editing for flat sections (LLM profiles, directories, scripts, integration settings), then the chat interface and task queue viewer.

Starting read-only followed Grafana's pattern and matched Assistant's trust principles. A viewer carried zero risk of mangling user files and validated the template structure before any file mutation code existed. Each increment was independently shippable and useful.

### HTMX + Alpine.js + DaisyUI, not an SPA

The frontend uses HTMX for page structure and data loading, Alpine.js for client-side form state in complex sections, and DaisyUI (Tailwind component library) for styling. No React, no Vue, no JavaScript build step.

We evaluated three approaches. Pure HTMX works for flat config but requires a server round-trip and dedicated partial template for every form interaction in nested structures. Estimate: 15-20 partials just for the automation rule editor. A full SPA (React with react-jsonschema-form) produces the best form-editing UX but requires Node.js, npm, a bundler, two test suites, and ongoing JS ecosystem maintenance. That's disproportionate for a config editor maintained by Python developers.

HTMX + Alpine.js is the middle ground. HTMX handles navigation and data persistence. Alpine handles in-flight form editing (add/remove automation rules, conditional fields, dynamic lists) without server round-trips. DaisyUI provides the collapse/accordion/tabs components that nested config editing needs. Everything loads from CDN or is vendored as static files. All server-side logic stays in Python.

### `ruamel.yaml` for round-trip editing

`ruamel.yaml` is a core dependency used for config writing. It preserves comments, key ordering, block style, and quoting when modifying and re-serializing YAML. PyYAML is still used for reading.

Gotchas: must use `typ='rt'` mode (without it, comments are silently dropped). The C extension kills comment preservation. Deleting list elements can orphan adjacent comments. No stable public API for comment manipulation. Prefer modifying values in-place over delete-and-recreate.

StrictYAML was considered but rejects custom YAML tags (`!secret`, `!yolo`). That's a dealbreaker.

---

## Server Configuration

### Default port 6767, not 8000

Assistant binds to port 6767 instead of the uvicorn default of 8000.

Port 8000 conflicts with llama.cpp's default server port. Since Assistant is designed to work with local LLM inference and llama.cpp is a common backend, running both on 8000 means one of them has to be reconfigured every time. Making Assistant the one that moves is the right call: llama.cpp's port is baked into model server scripts, docker-compose files, and other tooling that's harder to change. Assistant is one line in the supervisor.

6767 was chosen because it's not claimed by any well-known service and is easy to remember.

---

## Configuration Updates in the UI

### File-level locking via `fcntl` for RMW cycles

All configuration mutations in `app/ui/yaml_rw.py` use an exclusive lock on a separate `.lock` file during the Read-Modify-Write cycle.

Why: the UI allows multiple concurrent POST requests (e.g. updating an LLM profile and a script simultaneously). Without locking, one process could read the file, a second process writes to it, and the first process then overwrites those changes with its own stale data. Using a separate lockfile ensures atomic updates even for complex round-trip YAML editing.

### In-memory synchronization via `reload_config()`

The web process calls `reload_config()` immediately after any successful configuration write.

Why: Assistant uses a module-level `config` singleton loaded at startup. While the UI writes to `config.yaml` on disk, the running web server's memory remains stale. Explicitly reloading the singleton ensures that the Dashboard, navigation, and subsequent config views reflect the changes (like updated log paths or integration names) without requiring a full process restart. Note that the worker and scheduler processes still require a full restart to pick up changes, as they are separate processes.

### Broad exception catch around `reload_config()` in POST handlers

The `yaml_rw` layer wraps all write-time errors in `ConfigValidationError`, and `validate_proposed()` pre-flights the full `load_config()` pipeline on a temp copy before writing. But `reload_config()` runs the real `load_config()` after the file is already on disk — and that can raise `pydantic.ValidationError`, `ImportError` (integration discovery), or other exceptions not wrapped in `ConfigValidationError`. The window is narrow (validate_proposed exercises the same path) but non-zero for non-deterministic failures like module side effects.

Each POST handler catches `Exception` from `reload_config()`, logs the traceback, and returns a user-friendly 422 explaining that the file was saved but the reload failed. This prevents raw 500s and tells the user to restart the server. The broad catch is deliberate: the alternative is enumerating every exception `load_config()` could raise, which would drift as integrations are added.
