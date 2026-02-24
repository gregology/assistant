# System Architecture

GaaS runs as two processes that communicate through the filesystem.

## The two processes

**The FastAPI server** (`app/main.py`) handles three things: a REST API for manual triggers, a cron scheduler that enqueues tasks on a timer, and a health check endpoint. It does not process tasks itself.

**The worker** (`app/worker.py`) is a simple polling loop. It pulls the next task from the queue, looks up the handler by task type string, runs it, and marks the task as done or failed. That's it.

Both processes read the same config and write to the same filesystem directories. There is no shared memory, no message broker, no database. The filesystem is the coordination layer.

## Task queue

Tasks are YAML files that move between four directories:

```
data/queue/
  pending/    # Waiting to be picked up
  active/     # Currently being processed
  done/       # Completed successfully
  failed/     # Failed with error captured
```

Each file is named `{priority}_{timestamp}_{uuid}.yaml`. The priority prefix means a sorted directory listing returns tasks in priority order. Lower numbers go first.

Dequeue uses `os.rename()`, which is atomic on POSIX. If two workers race for the same file, one gets a `FileNotFoundError` and moves on. No locks needed.

Why filesystem instead of Redis or a proper message queue? Two reasons. First, you can `ls` the queue and see exactly what's happening. `pending/` has three files? Three tasks waiting. `failed/` has one? Something broke and you can read the YAML to see what. Second, it keeps memory usage low. RAM is better spent on LLM inference than on a queueing system.

A task must exist in exactly one directory at all times. The test suite enforces this invariant with stateful property testing that randomly interleaves queue operations and checks conservation after every step.

## Note store

All persistent data uses the same pattern: markdown files with YAML frontmatter.

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

The generic `NoteStore` class (`app/store.py`) handles reading, writing, and moving these files. Platform-specific stores like `EmailStore`, `PullRequestStore`, and `IssueStore` wrap it with domain methods, but the underlying storage is always markdown with frontmatter.

This means every piece of state in the system is human-readable. You can open any file in a text editor and see exactly what GaaS knows about an email or an issue, including the raw classification results.

## Integrations and platforms

Integrations live under `app/integrations/` as Python packages. Following the Home Assistant pattern, each integration contains **platforms** that handle specific resource types. The GitHub integration has `pull_requests` and `issues` platforms. The email integration has an `inbox` platform.

Each platform exports a `HANDLERS` dict mapping task type suffixes to handler functions. The integration's `__init__.py` aggregates them with platform prefixes:

```python
# github/platforms/pull_requests/__init__.py
HANDLERS = {
    "check": check_handle,
    "collect": collect_handle,
    "classify": classify_handle,
}

# github/__init__.py
from .platforms.pull_requests import HANDLERS as pr_handlers
HANDLERS = {}
for suffix, handler in pr_handlers.items():
    HANDLERS[f"pull_requests.{suffix}"] = handler
```

The top-level `app/integrations/__init__.py` registers these with the domain prefix, producing task types like `email.inbox.check` or `github.pull_requests.classify`. The worker routes tasks to handlers using these strings.

Each platform also has an entry task. This is the starting point when a schedule fires or someone hits the API. The scheduler enqueues entry tasks for each enabled platform within an integration. Entry tasks discover work (new emails, new PRs, new issues) and enqueue downstream tasks to process it.

There is no mandatory pipeline shape. Email uses a five-stage pipeline: `check -> collect -> classify -> evaluate -> act`. GitHub uses the same pattern. New integrations define whatever flow makes sense for their domain.

### Task priorities

Tasks enqueue downstream tasks with explicit priorities:

| Priority | Purpose |
|----------|---------|
| 3 | Discovery and collection (get data quickly) |
| 5 | Default |
| 6 | Classification (process after collection) |
| 7 | Actions (execute after classification) |
| 9 | Low confidence items (e.g. unauthenticated emails) |

## LLM abstraction

GaaS is backend-agnostic for LLM inference. Config defines named profiles (`default`, `fast`, etc.) with different `base_url`, `model`, `token`, and `parameters`. Integrations reference profiles by name.

The `LLMConversation` class manages multi-turn conversations and supports structured output with JSON schema validation. If the LLM returns something that doesn't match the schema, it retries up to three times.

Local inference via Ollama or any OpenAI-compatible endpoint is the default. Remote backends work too, but the config system warns users that data will leave their machine.

## Config

Configuration uses Home Assistant-inspired YAML with `!secret` references to a separate `secrets.yaml`. Both files are gitignored.

```yaml
llms:
  default:
    base_url: http://localhost:11434/v1
    model: llama3.2

integrations:
  - type: email
    name: personal
    imap_server: imap.example.com
    password: !secret email_password
    schedule:
      every: 30m
    platforms:
      inbox:
        automations:
          - when:
              is_noreply: true
            then: archive
```

Pydantic models validate everything at startup. Classification shorthand (`human: "is this a personal email?"`) gets normalized to full config objects. Schedule formats accept both `every: 30m` and `cron: "0 8-18 * * 1-5"`. Classifications and automations are configured per-platform rather than per-integration.
