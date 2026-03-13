# Assistant (Assistant)

Your inbox is full of messages that were written for the sender, not for you. Assistant reads them, classifies them, and acts on rules you define. Archive the noise. Draft replies for the stuff that matters. Every action is logged in plain markdown so you can see exactly what happened and why. Runs locally on your hardware. Nothing leaves your machine. Assistant is your _controllable_ AI personal assistant.

Assistant is built on 3 principles:

 - **Reversibility** - autonomous actions can be undone
 - **Audibility** - autonomous activities are logged for humans
 - **Accountability** - irreversable actions require a human

## Reversability

**❌ Sending an email**  
An email can be sent, it can not be unsent, this is a non-reversible action.

**✅ Drafting an email**  
A draft email can be created, it can be deleted, this is a reversible action.

**❌ Googling an acronym found in an email**  
A search query is sent to Google, private information has been sent to an untrusted system, this is a non-reversible action.

**✅ Searching the user's notes for an acronym found in an email**  
A grep command can scan a directory, no private information has left the system, this is a reversible action.

There are 3 dimensions of reversibility:
 - Complexity - resubscribing to a mailing list you unsubscribed from is technically possible but painful.
 - Temporal - A deleted email is only recoverable for 30 days.
 - Context - Turning off a heat pump is not reversible in sub-zero temperatures if it causes the pipes to freeze.

The human can determine the level of reversibility they are comfortable with.


## Installation

Assistant is in **alpha** so shit will break.

```bash
curl -fsSL https://gho.st/install.sh | bash
```

## Documentation

- [Why Assistant exists](docs/why.md) - The motivation and use cases
- [Design principles](docs/design.md) - WWHAD, zero trust, default to code, and the rest of the non-negotiables
- [API reference](docs/api.md) - Endpoints and examples
- Architecture
  - [System overview](docs/architecture/overview.md) - Components and data flow
  - [Safety model](docs/architecture/safety-model.md) - Reversibility, provenance, trust boundaries
  - [Audit log](docs/architecture/human-log.md) - Human-readable daily logging
- Testing
  - [Philosophy](docs/testing/philosophy.md) - Why we test the way we do
  - [Guide](docs/testing/guide.md) - Practical testing reference
- Integration user guides
  - [Email](packages/assistant-email/README.md) - Configuration, automations, condition keys
  - [GitHub](packages/assistant-github/README.md) - PR and issue tracking, classification
