# Building a Assistant Integration

You're here because someone wants a new integration for Assistant. This document tells you what to ask, what to read, and in what order.

Assistant integrations come in two flavors. Pick the right one first, then follow the recipe.

## What kind of integration is this?

**Platform integration** -- polls or listens to an external source (inbox, API, webhook), classifies what it finds with an LLM, and runs automations on the results. Email and GitHub are platforms. If the answer to "does this thing produce events we react to?" is yes, you want a platform.

**Service integration** -- a callable function that other integrations can invoke from automation rules. No polling, no events. Gemini web research is a service. If the answer to "does this get called as a side effect of some other automation?" is yes, you want a service.

Some integrations could go either way. Slack, for example: if you're polling channels for messages to classify, that's a platform. If you're sending a notification as an action from an email automation, that's a service. Ask the human which one they need. Could be both -- that's a hybrid, and the two halves are independent.

## Questions to ask before writing any code

These are in priority order. Get answers to all of them before you start.

### For any integration

1. **What's the domain name?** Single lowercase word, no hyphens. This becomes the package name (`assistant-{domain}`), the entry point key, and the config `type:` value. Examples: `email`, `github`, `gemini`, `slack`, `todoist`.

2. **What credentials or config does it need?** API keys, tokens, server URLs, account IDs. Each becomes a field in `config_schema` and the user puts the actual values in `config.yaml` (secrets go in `secrets.yaml` via `!secret` references).

3. **Does it have external dependencies?** Python packages beyond `assistant-sdk`. List them. They go in `pyproject.toml` under `dependencies` and in `manifest.yaml` under `dependencies` (the loader checks these at startup).

4. **What actions can it take?** For each action, ask: **can it be undone?** This is the most important question in the entire system. See `integration-guide/safety.md` for the reversibility framework. The answer determines how actions get classified, tested, and gated.

### For platform integrations

5. **What does it poll or listen to?** IMAP inbox, REST API, webhook, RSS feed, filesystem watcher. This shapes the `check` handler.

6. **What's the data model?** What fields does each item have? Which ones are deterministic (come from the source directly) vs. which ones need LLM classification? The deterministic ones go in `DETERMINISTIC_SOURCES`.

7. **What classifications make sense?** Confidence scores (0-1), booleans, enums. What questions would a human ask when triaging these items? These become the default classifications.

8. **How should items be stored as notes?** Filename convention, what goes in frontmatter vs. markdown body. Look at `EmailStore` and `GitHubEntityStore` for patterns.

### For service integrations

5. **What inputs does it need?** These go in `input_schema` in the manifest.

6. **What does it return?** Services return a dict. The default routing saves it as a markdown note. If the service doesn't return anything useful, it can return an empty dict.

7. **Does it send data externally?** If yes, it's irreversible by default. Cannot be triggered from LLM provenance without `!yolo`. This isn't optional -- the safety validation enforces it at config load time.

## File map

Once you know what you're building, here's what to read:

| Topic | File | When to read it |
|-------|------|-----------------|
| Full integration contract | `integration-guide/contract.md` | Always -- this is the spec |
| Safety model | `integration-guide/safety.md` | Always -- non-negotiable |
| Config patterns | `integration-guide/config.md` | When writing manifest.yaml and config_schema |
| Common code patterns | `integration-guide/patterns.md` | When writing handlers |
| Platform recipe | `integration-guide/recipe-platform.md` | Building a platform integration |
| Service recipe | `integration-guide/recipe-service.md` | Building a service integration |

## Reference implementations

Don't just read the docs. Read the actual code.

- **Platform (simple):** `packages/assistant-github/` -- two platforms (PRs and issues), read-only, no irreversible actions yet
- **Platform (full):** `packages/assistant-email/` -- IMAP polling, LLM classification, actions with reversibility tiers, custom store
- **Service:** `packages/assistant-gemini/` -- callable web research, irreversible (external API), result routing
- **SDK contracts:** `packages/assistant-sdk/` -- the models, evaluation engine, and runtime that all integrations depend on

## Hard rules

These are enforced by CI, safety tests, or the runtime itself. Not guidelines.

- Every action must be categorized by reversibility tier before you write tests for it.
- `SIMPLE_ACTIONS` is an allowlist. Unknown string actions are silently skipped at runtime.
- `IRREVERSIBLE_ACTIONS` from LLM/hybrid provenance are blocked at config load time unless tagged `!yolo`.
