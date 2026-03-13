# assistant-email

Polls an IMAP inbox, classifies emails with an LLM, and runs automations on the results. You can archive junk, draft replies to important threads, or auto-file calendar invites. Everything is configured in YAML.

## Prerequisites

- An IMAP-enabled email account
- An app password. Gmail, iCloud, and most providers require one for IMAP access. Your regular login password won't work.

## Config

```yaml
integrations:
  - type: email
    name: personal
    imap_server: imap.example.com
    imap_port: 993                        # Optional, defaults to 993
    username: you@example.com
    password: !secret personal_email_password
    schedule:
      every: 30m
    llm: default
    platforms:
      inbox:
        limit: 50                         # Max emails per check (default: 50)
        # window: 7d                      # Optional: only fetch emails from the last N days
        classifications:
          # ...
        automations:
          # ...
```

`imap_server`, `username`, and `password` are required. `imap_port` defaults to 993.

Credentials go in `secrets.yaml` and are referenced with `!secret`. Don't put passwords directly in `config.yaml`.

## Classifications

Classifications tell the LLM what to assess about each email. There are three types.

**Confidence** is the default. Returns a 0-1 float:

```yaml
classifications:
  human: is this a personal email written by a human?
```

That's shorthand. The expanded form looks like this:

```yaml
classifications:
  human:
    prompt: is this a personal email written by a human?
    type: confidence
```

**Boolean** returns true or false:

```yaml
classifications:
  requires_response:
    prompt: does this email require a response?
    type: boolean
```

**Enum** returns one value from a set:

```yaml
classifications:
  priority:
    prompt: what is the priority of this email?
    type: enum
    values: [low, medium, high, critical]
```

If you omit `classifications` entirely, the integration ships these defaults:

| Name | Type | Prompt |
|------|------|--------|
| `human` | confidence | is this a personal email written by a human? |
| `user_agreement_update` | boolean | is this email about a user agreement update? |
| `requires_response` | boolean | does this email require a response? |
| `priority` | enum | what is the priority of this email? (low/medium/high/critical) |

## Automations

Automations are `when`/`then` pairs. All conditions in a `when` block must match for the actions in `then` to fire.

### Conditions

Conditions can reference LLM classification results or deterministic email properties.

**LLM-based conditions** use the `classification.` prefix:

```yaml
# Confidence threshold (>= by default)
- when:
    classification.human: 0.8

# Explicit operator
- when:
    classification.human: "> 0.8"

# Boolean match
- when:
    classification.user_agreement_update: true

# Enum, exact match
- when:
    classification.priority: high

# Enum, any-of match
- when:
    classification.priority: [high, critical]
```

**Deterministic conditions** are resolved from IMAP data. No LLM involved:

```yaml
# Domain of the sender
- when:
    domain: example.com

# Root domain (strips subdomains, handles .co.uk etc)
- when:
    root_domain: company.com

# Noreply address
- when:
    is_noreply: true

# Calendar event
- when:
    is_calendar_event: true

# Authentication results
- when:
    authentication.dkim_pass: true
    authentication.spf_pass: true
```

The full list of deterministic sources: `authentication`, `calendar`, `domain`, `from_address`, `has_attachments`, `is_answered`, `is_calendar_event`, `is_forward`, `is_noreply`, `is_read`, `is_reply`, `is_starred`, `is_unsubscribable`, `root_domain`.

Multiple conditions in a `when` block use AND logic.

### Actions

| Action | Syntax | Reversibility |
|--------|--------|---------------|
| Archive | `archive` | Soft. Moves to Archive folder. |
| Spam | `spam` | Hard. Moves to Spam folder. |
| Trash | `trash` | Soft. Moves to Trash. |
| Unsubscribe | `unsubscribe` | **Irreversible.** Sends an RFC 8058 one-click unsubscribe POST. |
| Draft reply | `draft_reply: "message text"` | Soft. Creates a draft, doesn't send. |
| Move to folder | `move_to: "FolderName"` | Soft. Moves email to a named IMAP folder. |

`then` accepts a single action or a list:

```yaml
# Single action (shorthand)
then: archive

# Multiple actions
then:
  - archive
  - draft_reply: "Thanks, I'll take a look."
```

### Safety

Automations that depend on LLM output (anything with `classification.*` in `when`) are tracked as non-deterministic. Config validation will block you from triggering an irreversible action like `unsubscribe` from a non-deterministic condition.

If you really want to do it anyway, tag the action with `!yolo`:

```yaml
- when:
    classification.human: "< 0.1"
  then:
    - !yolo unsubscribe
```

That's your explicit acknowledgment that you're okay with the risk of LLM misclassification triggering something permanent.

Deterministic conditions like `domain` or `is_noreply` can trigger any action without `!yolo` because no LLM is involved.

## Full example

```yaml
integrations:
  - type: email
    name: personal
    imap_server: imap.example.com
    username: you@example.com
    password: !secret personal_email_password
    schedule:
      every: 30m
    llm: default
    platforms:
      inbox:
        limit: 50
        # window: 7d                      # Optional: only fetch emails from the last N days
        classifications:
          human: is this a personal email written by a human?
          requires_response:
            prompt: does this email require a response?
            type: boolean
        automations:
          # Archive past calendar events (deterministic, no LLM needed)
          - when:
              is_calendar_event: true
              calendar.end: "<now()"
            then: archive

          # Archive robot emails (LLM-based)
          - when:
              classification.human: "< 0.2"
            then: archive

          # Draft a reply for important human emails
          - when:
              classification.human: "> 0.8"
              classification.requires_response: true
            then:
              - draft_reply: "I'll review this shortly."
```
