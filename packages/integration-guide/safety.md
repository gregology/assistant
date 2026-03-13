# Safety Model

This is the part you can't skip. Assistant exists because autonomous AI actions need safety boundaries. Every integration participates in the safety model whether it wants to or not.

## Reversibility tiers

Before you write a single action handler, categorize every action your integration can take.

**Tier 1 -- Read-only.** Fetching data, saving notes locally. No external side effects. Most `check` and `collect` handlers live here.

**Tier 2 -- Soft reversible.** The action changes state but can be undone. Archiving an email (you can un-archive it). Pinning a message (you can unpin it). Most `SIMPLE_ACTIONS` live here.

**Tier 3 -- Hard reversible.** The action can technically be undone but the reversal has consequences. Sending a draft (you can send a retraction, but the recipient already saw it). Moving an issue to a different project.

**Tier 4 -- Irreversible.** Cannot be undone at all. Unsubscribing from a mailing list. Deleting a record. Sending data to an external API (you can't un-send it).

Tier 1 and 2 actions can be triggered freely from any provenance. Tier 3 and 4 actions go in `IRREVERSIBLE_ACTIONS` and get blocked from LLM/hybrid provenance unless the user explicitly tags them with `!yolo` in their config.

"But my action is only *slightly* irreversible" -- no. If you can't undo it with a single API call, it's irreversible. Err on the side of caution. Users can always override with `!yolo`.

## Provenance

Every automation rule has a provenance: `rule`, `llm`, or `hybrid`. The system determines this automatically from the condition keys in the `when` clause.

**`rule`** -- all conditions reference fields in your platform's `DETERMINISTIC_SOURCES`. No LLM involved. These are safe to trigger any action because the decision is fully deterministic.

```yaml
# rule provenance: "is_calendar_event" is in DETERMINISTIC_SOURCES
- when:
    is_calendar_event: true
  then: archive
```

**`llm`** -- all conditions reference `classification.*` fields. The LLM's judgment drives the decision.

```yaml
# llm provenance: "classification.human" is an LLM output
- when:
    classification.human: "< 0.2"
  then: archive
```

**`hybrid`** -- a mix of deterministic and classification conditions.

```yaml
# hybrid provenance: "domain" is deterministic, "classification.human" is LLM
- when:
    domain: "example.com"
    classification.human: "> 0.8"
  then:
    - !yolo unsubscribe
```

The provenance system is automatic. You don't call it directly. You just need to correctly populate `DETERMINISTIC_SOURCES` in your `const.py` so it knows which fields are deterministic.

## How it's enforced

At config load time, `app/config.py` runs `_validate_automation_safety()` for every platform. For each automation rule:

1. Resolves provenance from the `when` keys against your `DETERMINISTIC_SOURCES`
2. Checks if any actions are in your `IRREVERSIBLE_ACTIONS`
3. If an irreversible action has `llm` or `hybrid` provenance and isn't wrapped in `!yolo`, the entire automation is disabled and a warning is logged

This happens before the server starts. Bad configs don't silently pass.

At runtime, `act.py` enforces a second layer:

1. String actions not in `SIMPLE_ACTIONS` are skipped with a warning
2. Dict actions with unknown keys are skipped with a warning

Two layers of defense. The config validation catches it early. The runtime enforcement catches anything that slips through.

## !yolo

The `!yolo` YAML tag wraps an action to signal "I know this is irreversible from LLM provenance and I accept the risk." It's a custom YAML constructor that creates a `YoloAction` wrapper.

```yaml
# Scalar form
then:
  - !yolo unsubscribe

# Mapping form (for service/script actions)
then:
  - !yolo
    service:
      call: gemini.default.web_research
      inputs:
        prompt: "research {{ domain }}"
```

The safety validation unwraps `YoloAction` to inspect the underlying action but respects the override. Without `!yolo`, the same config would be rejected.

## Services and irreversibility

Services are irreversible by default. The manifest can declare `reversible: true`, but this is only correct if the service meets **both** criteria:

1. It's read-only (doesn't modify external state)
2. It doesn't transmit data beyond the system boundary

A service that queries a public API is still irreversible because it sends user-context data externally. The query might contain information extracted from emails or other private sources. You can't take back a network request.

If your service calls any external API, leave `reversible: false` (the default). Users who want LLM provenance to trigger it will use `!yolo`.

## Scripts

Same model as services. Scripts are irreversible by default because the system can't statically verify what shell code does. A script definition can declare `reversible: true` in `config.yaml`, but this is an explicit human judgment call.

## What goes in DETERMINISTIC_SOURCES

Every field your platform's snapshot exposes that comes directly from the data source, not from LLM output. Think of it as: "could I evaluate this condition without calling an LLM?"

For email, that's things like `domain`, `from_address`, `is_read`, `has_attachments`, `authentication.dkim_pass`.

For GitHub, it's `org`, `repo`, `author`, `status`, `additions`, `deletions`.

For a Slack integration, it might be `channel`, `author`, `is_thread`, `has_reactions`, `message_type`.

If you're unsure whether a field is deterministic, it probably is. Deterministic means "the value comes from the source API, not from an LLM prediction." Classification results are the only non-deterministic source in the current system.

## What goes in IRREVERSIBLE_ACTIONS

Only actions that genuinely cannot be undone. Err conservative.

For email: `unsubscribe` (RFC 8058 one-click, can't re-subscribe).

For GitHub: nothing yet (read-only).

For a hypothetical Slack integration: `delete_message` would be irreversible. `pin` would not. `send_message` is debatable -- you can delete it, but recipients may have already seen it. When in doubt, put it in `IRREVERSIBLE_ACTIONS`. Users can always override with `!yolo`.

## What goes in SIMPLE_ACTIONS

The allowlist of string actions your `act` handler accepts. This is a safety boundary: the act handler should check every string action against this set and skip anything not in it.

```python
def _execute_action(item, action) -> None:
    if isinstance(action, str):
        if action not in SIMPLE_ACTIONS:
            log.warning("unknown action %r, skipping", action)
            return
        getattr(item, action)()
```

Dict actions (like `{"draft_reply": "..."}` or `{"move_to": "folder"}`) are handled separately with explicit key checks.

Start with the minimum set. Add actions one at a time as you implement them, with a reversibility assessment for each. The CLAUDE.md is clear on this: "The `SIMPLE_ACTIONS` set must not grow without deliberate reversibility review."
