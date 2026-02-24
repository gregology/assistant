# Email Integration

Connects to IMAP mailboxes, classifies emails with an LLM, and executes automated actions based on classification results and deterministic email properties.

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
- [Condition Keys](#condition-keys)
  - [Deterministic (rule-based)](#deterministic-rule-based)
  - [LLM Classifications](#llm-classifications)
- [Actions](#actions)
- [Automations](#automations)
  - [Condition syntax](#condition-syntax)
  - [Provenance and safety](#provenance-and-safety)
  - [The `!yolo` override](#the-yolo-override)
- [Automation Examples](#automation-examples)
- [Pipeline](#pipeline)

---

## Quick Start

```yaml
integrations:
  - type: email
    name: personal
    imap_server: imap.example.com
    username: you@example.com
    password: !secret personal_email_password
    schedule:
      every: 30m
    platforms:
      inbox:
        automations:
          - when:
              is_noreply: true
            then:
              - archive
```

---

## Configuration Reference

```yaml
integrations:
  - type: email
    name: personal                  # Unique name used in logs and note paths
    imap_server: imap.example.com
    imap_port: 993                  # Default: 993
    username: you@example.com
    password: !secret email_pass    # Reference a key from secrets.yaml
    schedule:
      every: 30m                    # or: cron: "0 8-18 * * 1-5"
    llm: default                    # LLM profile name from the llms: section
    platforms:
      inbox:
        limit: 50                   # Max emails fetched per run. Default: 50
        classifications: ...        # See below
        automations: ...            # See below
```

IMAP credentials and schedule are set at the integration level. Classifications, automations, and limit are set per-platform under `platforms.inbox`.

---

## Condition Keys

Automation `when` conditions reference these keys. All conditions in a `when` block use **AND semantics**. Every condition must match for the automation to fire.

### Deterministic (rule-based)

These are resolved from the email itself, no LLM involved. Using only these keys gives your automation `rule` provenance, which allows irreversible actions without a `!yolo` override.

#### Email identity

| Key | Type | Description |
|-----|------|-------------|
| `domain` | string | Sender domain extracted from `From:` header (e.g. `stripe.com`) |
| `from_address` | string | Full sender address (e.g. `alerts@github.com`) |
| `is_noreply` | boolean | Sender matches `no-reply`, `do-not-reply`, `mailer-daemon`, or `postmaster` |
| `is_reply` | boolean | `In-Reply-To` header is present, this email is a reply to another |
| `is_forward` | boolean | Subject starts with `Fwd:`, `FW:`, `Fw:`, or `[Fwd:` |
| `is_unsubscribable` | boolean | Email has RFC 8058 one-click unsubscribe (both `List-Unsubscribe` URL and `List-Unsubscribe-Post` headers) |

#### Authentication

| Key | Type | Description |
|-----|------|-------------|
| `authentication.dkim_pass` | boolean | DKIM signature verified |
| `authentication.spf_pass` | boolean | SPF check passed |
| `authentication.dmarc_pass` | boolean | DMARC policy passed |

#### Calendar events

Available when the email contains an `.ics` attachment (`is_calendar_event: true`).

| Key | Type | Description |
|-----|------|-------------|
| `is_calendar_event` | boolean | Email has a calendar attachment |
| `calendar.method` | string | iTIP method: `request`, `cancel`, `reply` |
| `calendar.is_update` | boolean | `true` when `method=request` and `SEQUENCE > 0` (rescheduled/edited event) |
| `calendar.partstat` | string | Reply status for `method=reply`: `accepted`, `declined`, `tentative` |
| `calendar.start` | string | Event start time as ISO 8601 string |
| `calendar.end` | string | Event end time as ISO 8601 string |
| `calendar.guest_count` | integer | Number of attendees listed in the invite |

### LLM Classifications

These are resolved by asking the LLM and are only available after `email.inbox.classify` runs. Using these keys gives your automation `llm` provenance, which blocks irreversible actions unless `!yolo` is set.

Classifications are defined per-platform under `platforms.inbox.classifications`. If omitted, the defaults below apply.

#### Default classifications

| Key | Type | Prompt |
|-----|------|--------|
| `classification.human` | confidence | Is this a personal email written by a human? |
| `classification.user_agreement_update` | boolean | Is this email about a user agreement update? |
| `classification.requires_response` | boolean | Does this email require a response? |
| `classification.priority` | enum (`low`, `medium`, `high`, `critical`) | What is the priority of this email? |

#### Defining custom classifications

```yaml
platforms:
  inbox:
    classifications:
      # Shorthand: string value becomes a confidence classification
      human: is this a personal email written by a human?
      robot: is this email from an automated system or mailing list?

      # Boolean
      requires_response:
        prompt: does this email require a response from me?
        type: boolean

      # Enum
      urgency:
        prompt: how urgent is this email?
        type: enum
        values: [none, low, medium, high, critical]
```

---

## Actions

Actions are listed under `then:`. A single action can be written as a bare string. Multiple actions require a list.

### Folder actions (move email between IMAP folders)

When a folder action fires, the associated note in `notes/emails/{name}/` is moved to a matching subdirectory, keeping notes in sync with the mailbox.

| Action | IMAP folder | Note location | Reversibility |
|--------|-------------|---------------|---------------|
| `archive` | `\Archive` | `notes/emails/{name}/archive/` | Soft, easily moved back |
| `trash` | `\Trash` | `notes/emails/{name}/trash/` | Soft, recoverable until folder is emptied |
| `spam` | `\Junk` | `notes/emails/{name}/spam/` | Hard, may train server spam filters |

### Other actions

| Action | Description | Reversibility |
|--------|-------------|---------------|
| `move_to: "FolderName"` | Move to any named IMAP folder. Note mirrors to `notes/emails/{name}/FolderName/`. Supports nested folders (`Work/Stripe`). | Soft |
| `unsubscribe` | HTTP POST one-click unsubscribe (RFC 8058). Only fires if `is_unsubscribable` would be true. | **Irreversible** |
| `draft_reply: "text"` | Creates a draft reply in `\Drafts`. Never sends. | Soft |

### Shorthand syntax

```yaml
# These are equivalent:
then: archive

then:
  - archive
```

---

## Automations

### Condition syntax

#### Confidence (float 0-1)

```yaml
# Numeric: fires when value >= threshold
classification.human: 0.8

# Operator string: full comparison control
classification.human: "> 0.8"
classification.robot: "<= 0.3"
classification.human: ">= 0.9"
```

#### Boolean

```yaml
authentication.dkim_pass: true
is_noreply: false
classification.requires_response: true
```

#### String (exact match)

```yaml
domain: github.com
from_address: alerts@example.com
calendar.method: cancel
calendar.partstat: accepted
```

#### List (any-of match)

```yaml
domain: [github.com, gitlab.com]
classification.urgency: [high, critical]
calendar.partstat: [accepted, tentative]
```

### Provenance and safety

Every automation is assigned a **provenance** based on which condition keys it uses:

| Provenance | Condition keys used | Can run irreversible actions? |
|------------|--------------------|-----------------------------|
| `rule` | Deterministic only | Yes |
| `llm` | LLM classification only | No (unless `!yolo`) |
| `hybrid` | Mix of both | No (unless `!yolo`) |

The only irreversible action is `unsubscribe`. `archive`, `trash`, `spam`, and `draft_reply` are all reversible and are never blocked.

### The `!yolo` override

Tag an action with `!yolo` to explicitly acknowledge the risk of running it from non-deterministic provenance. This is a deliberate, auditable escape hatch, not a workaround.

```yaml
- when:
    classification.robot: "> 0.95"
  then:
    - !yolo unsubscribe
```

A warning is logged at startup for every `!yolo`-tagged automation. The action will still execute, but you've made a deliberate choice and that choice is visible in the config and the logs.

---

## Automation Examples

### Archive no-reply and automated emails

```yaml
platforms:
  inbox:
    automations:
      - when:
          is_noreply: true
        then:
          - archive

      - when:
          classification.robot: "> 0.85"
        then:
          - archive
```

### Handle calendar invitations

```yaml
platforms:
  inbox:
    automations:
      # Archive accepted/declined replies, no action needed
      - when:
          is_calendar_event: true
          calendar.method: reply
          calendar.partstat: [accepted, declined]
        then:
          - archive

      # Trash cancelled events
      - when:
          is_calendar_event: true
          calendar.method: cancel
        then:
          - trash

      # Archive event updates (rescheduled/edited)
      - when:
          is_calendar_event: true
          calendar.is_update: true
        then:
          - archive
```

### Unsubscribe from mailing lists (deterministic)

Deterministic provenance, no `!yolo` needed because the condition is rule-based, not LLM-based.

```yaml
platforms:
  inbox:
    automations:
      - when:
          is_noreply: true
          is_unsubscribable: true
          authentication.dkim_pass: true
        then:
          - unsubscribe
```

### Unsubscribe from mailing lists (LLM-assisted)

LLM provenance, requires `!yolo` because `unsubscribe` is irreversible.

```yaml
platforms:
  inbox:
    automations:
      - when:
          classification.robot: "> 0.95"
          is_unsubscribable: true
        then:
          - !yolo unsubscribe
```

### Draft reply to urgent human emails

```yaml
platforms:
  inbox:
    automations:
      - when:
          classification.human: "> 0.8"
          classification.requires_response: true
          classification.urgency: [high, critical]
        then:
          - draft_reply: "Thanks for reaching out, I'll review this shortly."
```

### Trusted sender shortcut (fully deterministic)

All conditions are rule-based, so this has `rule` provenance and could safely gate irreversible actions.

```yaml
platforms:
  inbox:
    automations:
      - when:
          authentication.dkim_pass: true
          authentication.spf_pass: true
          authentication.dmarc_pass: true
          domain: work.com
        then:
          - draft_reply: "On it."
```

### File emails into named folders

```yaml
platforms:
  inbox:
    automations:
      - when:
          domain: github.com
        then:
          - move_to: GitHub

      - when:
          domain: [notion.so, linear.app, figma.com]
        then:
          - move_to: Work/SaaS

      - when:
          classification.user_agreement_update: true
        then:
          - move_to: Legal
```

### Spam a known bad domain

```yaml
platforms:
  inbox:
    automations:
      - when:
          domain: [spam-domain.com, phishing-co.net]
        then:
          - spam
```

### Layered rules (order matters, all matching automations fire)

```yaml
platforms:
  inbox:
    automations:
      # First: unsubscribe from anything unsubscribable and robotic
      - when:
          classification.robot: "> 0.9"
          is_unsubscribable: true
        then:
          - !yolo unsubscribe

      # Second: archive anything else that looks automated
      - when:
          classification.robot: "> 0.75"
        then:
          - archive

      # Third: draft reply to anything human that needs a response
      - when:
          classification.human: "> 0.8"
          classification.requires_response: true
        then:
          - draft_reply: "I'll get back to you soon."
```

All matching automations fire, not just the first match. Design rules so their action sets don't conflict (e.g. avoid `archive` and `trash` firing on the same email).

---

## Pipeline

```
email.inbox.check (priority 3)
  Fetches all UIDs from the IMAP server. Compares against known UIDs in the
  note store (including archive/, trash/, spam/ subdirectories). Enqueues
  email.inbox.collect for each new email.

email.inbox.collect (priority 3)
  Downloads the email by UID. Saves it as a markdown note with frontmatter.
  Checks authentication results:
    - All pass -> enqueue email.inbox.classify at priority 6
    - Any fail -> enqueue email.inbox.classify at priority 9 (processed last)

email.inbox.classify (priority 6 or 9)
  Runs LLM classification. Updates the note with results.
  Evaluates automation rules. If any match, enqueues email.inbox.act.

email.inbox.act (priority 7)
  Executes IMAP actions (archive, trash, spam, unsubscribe, draft_reply).
  For folder actions, mirrors the move to the note store subdirectory.
```

### Note store layout

```
notes/emails/{integration-name}/
  2026_01_15_09_30_00__37001.md   # Active inbox notes
  2026_01_14_14_27_36__37927.md
  archive/
    2026_01_10_08_00_00__36800.md # Archived
  trash/
    2026_01_12_11_00_00__36900.md # Trashed
  spam/
    2026_01_13_07_45_00__36950.md # Marked as spam
```

Each note is a markdown file with YAML frontmatter containing the email metadata and classification results, making every automated decision human-readable and auditable.
