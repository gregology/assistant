# GaaS (Greg as a Service)

GaaS is built on 3 principles:

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

GaaS is in **alpha** so shit will break.

```bash
curl -fsSL https://raw.githubusercontent.com/gregology/GaaS/main/install.sh | bash
```


## Design Principles

### WWHAD

_What would Home Assistant Do?_ Home Assistant has battle hardened patterns for complex configurations and intuitive UIs to set those configurations.

### Human readable

Every "decision" should leave a human readable audit trail.  
Note: I put "decision" in quotation marks because LLMs do not make decisions, they make next token predictions.

### Default to code

Asking an LLM to do a programmable task is the robot equivalent of _this meeting could have been an email_. Don't burn tokens trying to convince a non deterministic machine to do a programmable task.

### Zero trust

Patterns should not rely on trusting a non deterministic machine.

### Optimize for memory

Memory is most valuable for inference. The disk based queueing system was added for human readability _and_ to reduce memory usage.

### Provenance

Not all classification is equal. An email classified by an LLM is a probabilistic guess. An email classified by its domain name is deterministic. The system tracks this distinction as **provenance** (`rule`, `llm`, or `hybrid`) and uses it to gate irreversible actions. Automations with LLM provenance cannot trigger irreversible actions unless explicitly overridden with `!yolo`.


## Documentation

- [Why GaaS exists](docs/why.md) - The motivation and use cases
- [API reference](docs/api.md) - Endpoints and examples
- Architecture
  - [System overview](docs/architecture/overview.md) - Components and data flow
  - [Safety model](docs/architecture/safety-model.md) - Reversibility, provenance, trust boundaries
  - [Audit log](docs/architecture/human-log.md) - Human-readable daily logging
- Testing
  - [Philosophy](docs/testing/philosophy.md) - Why we test the way we do
  - [Guide](docs/testing/guide.md) - Practical testing reference
- Integration user guides
  - [Email](app/integrations/email/README.md) - Configuration, automations, condition keys
  - [GitHub](app/integrations/github/README.md) - PR and issue tracking, classification
