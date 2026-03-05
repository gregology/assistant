# gaas-email

The email integration, extracted from the original `app/integrations/email/`. Handles IMAP inbox polling, LLM classification, automation evaluation, and action execution. All imports point at `gaas_sdk.*`, not `app.*`.

Discovered at startup via Python entry points (`[project.entry-points."gaas.integrations"]` in pyproject.toml). Can be shadowed by a local override in `app/integrations/email/` or a custom integrations directory during development.

## Structure

```
src/gaas_email/
  __init__.py
  mail.py                    # Mailbox client, IMAP connection, email model
  manifest.yaml              # Integration manifest (domain, config schema, platforms)
  platforms/
    inbox/
      __init__.py            # Exports HANDLERS dict
      check.py               # Entry task: poll IMAP, discover new messages
      collect.py             # Download and parse individual emails
      classify.py            # LLM classification using Jinja prompt templates
      evaluate.py            # Evaluate automations against classification results
      act.py                 # Execute actions (archive, spam, trash, unsubscribe, draft_reply, move_to)
      store.py               # EmailStore wrapping NoteStore with email-specific methods
      const.py               # Safety constants
      templates/
        classify.jinja       # Classification prompt with salt-based injection defense
```

## Safety constants (`const.py`)

- **DETERMINISTIC_SOURCES**: `authentication`, `calendar`, `domain`, `from_address`, `has_attachments`, `is_answered`, `is_calendar_event`, `is_forward`, `is_noreply`, `is_read`, `is_reply`, `is_starred`, `is_unsubscribable`, `root_domain`
- **IRREVERSIBLE_ACTIONS**: `unsubscribe`
- **SIMPLE_ACTIONS**: `archive`, `spam`, `trash`, `unsubscribe` -- the allowlist. Unknown actions are skipped, never executed. This set must not grow without reversibility review.

## Pipeline

`check -> collect -> classify -> evaluate -> act`

Each stage enqueues the next as a separate queue task. The evaluate stage calls `enqueue_actions()` from `gaas_sdk.actions` to partition script/service actions from platform actions.

## Tests

```
tests/
  test_act.py             # Action execution, allowlist enforcement
  test_check.py           # Window parsing, inbox fetch ordering and IMAP criteria
  test_classify.py        # Condition matching, operators, schema building
  test_email_store.py     # EmailStore CRUD, move, dedup
  test_mail_parsing.py    # Header parsing (auth, unsubscribe, dates, calendar)
```

Run in isolation (no app config needed):

```bash
uv run pytest packages/gaas-email/tests/
```

Tests import from `gaas_sdk.*` directly, not through `app.*` re-export shims. This means they run without loading the app config singleton.

## Key patterns

**mail.py**: The `Mailbox` class wraps `imap-tools` with context manager support. Email objects expose parsed headers, authentication results (DKIM/DMARC/SPF), calendar events, and unsubscribe capability. `Received:` header is used for timestamps instead of `Date:` (which is sender-controlled). `inbox_message_ids()` fetches newest first (`reverse=True`) and accepts an optional `since` date for IMAP `SINCE` filtering. The `since` param is day-granularity because that's all IMAP supports.

**check.py**: The entry task reads `limit` and `window` from platform config. `window` is an opt-in string like `"7d"` that gets parsed into a `since` date by `_parse_window_days()`. Only days are accepted (not hours or minutes) because IMAP `SINCE` has no time component. When no window is configured, all inbox messages are considered.

**Unsubscribe**: RFC 8058 one-click only. Requires both `List-Unsubscribe` (HTTP URL) and `List-Unsubscribe-Post` headers. HTTP POST method per the spec. This is irreversible.

**Draft reply**: Preserves threading via `In-Reply-To` and `References` headers.

**IMAP folder discovery**: `_discover_folders()` matches special-use flags (`\Archive`, `\Drafts`, etc.), not hardcoded folder names.
