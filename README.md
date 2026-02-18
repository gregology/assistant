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
Note: I put "decision" in quotation marks because LLMs do not make decisions, they make next token probability calculations.

### Default to code

Asking an LLM to do a programmable task is the robot equivalent of _this meeting could have been an email_. Don’t burn tokens trying to convince a non deterministic machine to do a programmable task.

### Zero trust

Patterns should not rely on trusting a non deterministic machine, no matter how much it glazes you.

### Optimize for memory

Memory is most valuable for inference. The disk based queueing system was added for human readability _and_ to reduce memory usage.


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
