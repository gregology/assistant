# GaaS (Greg as a Service)

Like OpenClaw but with elastic bands around the pinchers.


## GaaS Manifesto

### The Principle of Reversibility

Every action that GaaS takes autonomously is reversible.

❌ Sending an email  
An email can be sent, it can not be unsent, this is a non-reversible action.

✅ Drafting an email  
A draft email can be created, it can be deleted, this is a reversible action.

❌ Googling an acronym found in an email  
A search query is sent to Google, private information has been sent to an untrusted system, this is a non-reversible action.

✅ Searching the user's notes for an acronym found in an email  
A grep command can scan a directory, no private information has left the system, this is a reversible action.

✅ Searching a local Wikipedia instance for an acronym found in an email
`kiwix-search` is used to query a ZIM file, no private information has left the system, this is a reversible action.

Not all reversibility is equal. Unarchiving an email is trivial. Resubscribing to a mailing list you unsubscribed from is technically possible but painful. Deleting an email is reversible within a 30 day retention window and then it is not. Turning off a heat pump is reversible unless the pipes freeze overnight. The four reversibility tiers used in the testing philosophy are a starting point. As GaaS expands into new integrations the model will need to account for difficulty, time windows, and context.

### The Principle of Audibility

Every action the AI makes should be auditable.
Log what the agent does, don't ask it what it did.

### The Principle of Accountability

AI has ability but no accountability.
Every non reversible action requires human-in-the-loop.


## Design Principles

### WWHAD

What would Home Assistant Do? Home Assistant has battle hardened patterns for complex configurations and intuitive UIs to set those configurations.

### Human readable

Every "decision" should leave a human readable audit trail.  
Note: I put "decision" in quotation marks because LLMs do not make decisions, they make next token predictions.

### Default to code

Asking an LLM to do a programmable task is the robot equivalent of _this meeting could have been an email_. Don’t burn tokens trying to convince a non deterministic machine to do a programmable task.

### Zero trust

Patterns should not rely on trusting a non deterministic machine, no matter how much it glazes you.

### Optimize for memory

Memory is most valuable for inference. The disk based queueing system was added for human readability _and_ to reduce memory usage.

### Know your sources

Not all classification is equal. An email classified by an LLM is a probabilistic guess. An email classified by its domain name is deterministic. Actions downstream should know whether their input came from a non deterministic system or a deterministic one. This distinction may inform safety thresholds, gating requirements, or whether human approval is needed. This is an open design problem and the implementation is not yet settled, but the principle should inform how new classification and automation logic is written.

### Testing philosophy

Tests exist to enforce the Principle of Reversibility. The purpose of the test suite is not to reduce bugs generally, it is to guarantee that automated actions cannot cause irreversible harm.

Test rigor should be proportional to how irreversible an action is, not how complex the code is. A one line HTTP POST that unsubscribes from a mailing list deserves more test rigor than 100 lines of email parsing, because the parsing can never leak data or trigger an irreversible action. Every action in the system should be categorized by reversibility tier:

- **Read only** (no side effects): checking a mailbox, classifying content, reading local files. Standard unit tests.
- **Soft reversible** (easily undone): archiving a message, creating a draft. Filesystem snapshot assertions.
- **Hard reversible** (technically undoable but with side effects): marking as spam may train server side filters. Shadow and dry run verification.
- **Irreversible** (cannot be undone): unsubscribing from a list, sending data to an external service. Property based safety invariants and mandatory dry run.

As new integrations and actions are added, each one should be placed into a tier before any tests are written. The tier determines the testing strategy, not the complexity of the implementation.

**Test the decision boundary, not the LLM.** The LLM is non deterministic by nature so asserting on its output is meaningless. But the automation dispatch logic that evaluates classification results and decides which actions to fire is entirely deterministic. This is where a bug becomes an irreversible action.

Use property based testing to verify that for all possible classification outputs, no unknown action is ever produced and no unsafe combination of actions can occur. For example, archiving a message and drafting a reply to it should never happen in the same automation run.

Prompt injection testing follows the same principle. Untrusted content (email bodies, PR descriptions, any external input) is fed directly into LLM prompts. Rather than trying to assert that the LLM successfully ignored an injection attempt, feed adversarial inputs through the full pipeline and assert on the actions produced. The prompt level defenses like random salts are the first barrier. The deterministic dispatch rules are the second. Tests should verify that even if the first barrier fails completely, the second barrier still prevents irreversible actions.

**Assert on filesystem state as an atomic value.** The filesystem is the database. The task queue moves YAML files between directories, integrations store markdown files with frontmatter, and logs append to daily files. Asserting on individual files misses emergent problems like orphaned tasks, files stuck in an intermediate state after a crash, or duplicate entries across directories.

Instead, snapshot the entire directory tree and assert on it as a single comparable value. After a full task lifecycle of `enqueue -> dequeue -> complete`, the invariant is: pending is empty, active is empty, done has exactly one file, and the total task count is conserved. No task should ever exist in two directories at once. No task should disappear without being accounted for. These structural assertions catch a class of bugs that per file checks will miss, particularly around crash recovery and concurrent access.

**Safety invariants must hold under chaos.** The dangerous failure mode is not the LLM being unavailable. Retry logic handles that. The dangerous failure is the LLM being confidently wrong. A model that classifies a phishing email as high confidence and requiring a response would trigger a draft reply to a malicious sender.

Chaos testing should inject faults at the classification level: flip boolean values, max out all confidence scores to 1.0, swap enum values to their most dangerous option. Then assert that safety boundaries still hold. For any possible classification output, the system should enforce bounded blast radius. No single automation run should trigger more than a configurable number of irreversible actions. These invariants should be expressed as properties that hold for all inputs using property based testing, not as example assertions against specific test fixtures. A property test that says "for all possible classifications, the blast radius is bounded" is a fundamentally stronger guarantee than an example test that says "for this one test email, archive was produced."


## Setup

```bash
uv sync
```

### Run server

Development server (auto-reload, localhost only):

```bash
uv run fastapi dev
```

Production server:

```bash
uv run fastapi run
```

### Run worker

The task queue worker polls for pending tasks and processes them. Run it in a separate terminal alongside the API server:

```bash
uv run python -m app.worker
```

### Run tests

```bash
uv run pytest -v
```

## API

```bash
# List configured integrations
GET /integrations

# Manually trigger an integration
POST /integrations/{type}/{name}/run
```

Examples:

```bash
curl -X POST http://localhost:8000/integrations/email/personal/run
curl -X POST http://localhost:8000/integrations/github/personal/run
```

Integrations also run automatically on their configured schedule when the server is running. Scheduled and manual triggers produce identical task queue entries.
