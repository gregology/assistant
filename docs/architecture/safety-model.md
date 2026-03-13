# Safety Model

Assistant has three core safety principles. Every feature in the system must respect all of them.

**Reversibility.** Every autonomous action must be undoable. Draft instead of send. Archive instead of delete. Search local notes instead of sending data to Google. If an action cannot be undone, it requires human approval.

**Audibility.** Every action the AI takes must be logged in a way humans can read. The system logs what the agent does. It does not ask the agent what it did. See [human-log.md](human-log.md) for how this works in practice.

**Accountability.** AI has ability but no accountability. The LLM classifies. Deterministic code decides what to do with that classification. The deterministic layer is the safety boundary, not the prompt.

## Reversibility tiers

Not all reversibility is equal. Unarchiving an email is trivial. Resubscribing to a mailing list you unsubscribed from is technically possible but painful. The system categorizes every action into one of four tiers, and that tier determines how much testing and gating the action requires.

| Tier | What it means | Examples |
|------|--------------|----------|
| Read-only | No side effects at all | Checking a mailbox, classifying content, reading local files |
| Soft reversible | Easily undone | Archiving a message, creating a draft, moving to a folder |
| Hard reversible | Technically undoable but with side effects | Marking as spam (may train server-side filters) |
| Irreversible | Cannot be undone | Unsubscribing from a list, sending data to an external service |

When adding a new action to the system, you categorize it into a tier *before* you write any code or tests. The tier drives everything downstream: what testing strategy to use, whether the action needs human approval, and whether it can be triggered by LLM classification at all.

Some reversibility is also time-dependent. Deleting an email is recoverable within a 30-day retention window, then it isn't. Turning off a heat pump is reversible unless the pipes freeze overnight. The four tiers are a starting point. As Assistant expands into new domains the model will need to account for difficulty, time windows, and context.

## Provenance

Every automation rule gets a provenance label based on what kind of conditions it uses. This determines whether the rule can trigger irreversible actions.

| Provenance | How it's assigned | Can trigger irreversible actions? |
|-----------|-------------------|-----------------------------------|
| `rule` | All conditions are deterministic (domain matching, header checks, IMAP flags) | Yes |
| `llm` | All conditions use LLM classification results | No |
| `hybrid` | Mix of deterministic and LLM conditions | No |

The logic is straightforward. If a condition was evaluated by deterministic code (e.g., "this email is from noreply@example.com"), you can trust it. If a condition was evaluated by a non-deterministic LLM (e.g., "this email is probably automated"), you cannot trust it enough to take an action you can't undo.

Each platform defines its own `DETERMINISTIC_SOURCES` and `IRREVERSIBLE_ACTIONS` sets in `const.py`. The provenance check at startup iterates all integrations, then all platforms within each integration, loading the relevant constants and validating each automation rule.

If a platform's `const.py` is missing or fails to load, the system applies a fail-safe default: all conditions are treated as non-deterministic (LLM provenance) and all actions are treated as irreversible. This blocks any automation with conditions and actions from firing. A warning is logged so integration authors can diagnose the issue. This matches the existing pattern where scripts and services default to irreversible.

Script actions are also subject to provenance gating. Scripts are **irreversible by default** because the system can't statically verify what shell code does. A script definition can opt in to reversibility with `reversible: true`, which allows it to fire from `llm`/`hybrid` provenance without `!yolo`. Without that flag, script actions follow the same rules as `unsubscribe`: blocked from non-deterministic provenance unless explicitly overridden.

Service actions follow the same pattern. Services declared in an integration's `manifest.yaml` are irreversible by default. The manifest can declare `reversible: true` for services that are both read-only **and** do not transmit data beyond the system boundary (e.g., a local file search or a localhost-only API call). Irreversible services from LLM provenance are blocked unless wrapped in `!yolo`.

### The `!yolo` override

You're a grown up and sometimes you want to do silly things like let an LLM-triggered automation do something irreversible. The `!yolo` tag is an explicit, auditable escape hatch for this.

```yaml
- when:
    classification.robot: "> 0.95"
    is_unsubscribable: true
  then:
    - !yolo unsubscribe
```

Every `!yolo`-tagged automation generates a warning at startup. It still executes, but you've made a deliberate choice and that choice is visible in the config and the logs.

## Trust boundaries

The system has two trust boundaries that work independently.

### Boundary 1: Prompt-level defense

Untrusted content (email bodies, PR descriptions, issue bodies, anything from the outside world) goes through LLM prompts rendered with Jinja2 templates. Each template uses random salt markers (`secrets.token_hex(4)`) to wrap untrusted content. The markers change every invocation, making it harder for injected instructions to predict the prompt structure.

This is a probabilistic defense. It makes prompt injection harder, but it cannot prevent it.

### Boundary 2: Deterministic dispatch

The automation dispatch layer (`evaluate_automations`) is entirely deterministic. It evaluates `when`/`then` rules against classification results and produces a list of actions. This code does not use any LLM. It's a pure function from classification results to action lists.

The dispatch layer enforces:

- Only known actions can be produced. Unknown strings are rejected with a warning.
- The `SIMPLE_ACTIONS` allowlist in `const.py` controls what actions exist at all.
- Irreversible actions are blocked from `llm` and `hybrid` provenance (unless `!yolo`) — at config load time (primary gate) and again at execution time in `act.py` (defense-in-depth).
- Missing classification keys cause automations to silently not fire (safe default).

The key insight is that these two boundaries are independent. Even if the prompt barrier fails completely and an attacker manipulates the LLM into classifying a phishing email as "high priority, requires response," the dispatch layer still controls what actions can actually happen. The worst outcome from a manipulated classification is a draft reply or an archive, both of which are reversible.

## What this means for new code

If you're adding a new action:

1. Categorize it by reversibility tier first
2. For platform-specific actions: add to `SIMPLE_ACTIONS` or dict action handling in `act.py`. For cross-cutting actions: add to the shared action layer in `app/actions/`
3. If it's irreversible, it cannot fire from `llm` or `hybrid` provenance without `!yolo`
4. Write tests proportional to the tier (see [testing philosophy](../testing/philosophy.md))
5. Use `log.human()` so the action appears in the [daily audit log](human-log.md)

If you're adding a script: define it in the `scripts:` section of `config.yaml`. Scripts are irreversible by default. Set `reversible: true` only if you're confident the script's effects can be undone.

If you're adding a service: declare it in the integration's `manifest.yaml` under `services:`. Services are irreversible by default. Set `reversible: true` only for read-only services with no side effects. The `call` + `inputs` syntax in automation `then` clauses triggers services the same way scripts are triggered.
