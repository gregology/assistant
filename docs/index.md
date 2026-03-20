# Assistant

An AI-powered personal assistant that processes emails, pull requests, issues, and other inputs using LLMs — safely.

Assistant uses AI to sort through the noise of digital communication, classifying messages and taking action based on rules you define. It transforms sender-benefit communication into receiver-benefit communication, so what reaches you is actually worth your time.

## Core Principles

**Reversibility** — Every autonomous action must be reversible. Draft instead of send. Archive instead of delete. If an action cannot be undone, it requires human approval.

**Audibility** — Every action the AI takes must be auditable. The filesystem is the database: task queues are YAML files you can inspect, notes are markdown, and daily logs are human-readable.

**Accountability** — AI has ability but no accountability. The LLM classifies; deterministic code decides. The dispatch layer is the safety boundary, not the LLM.

## Learn More

- [Why Assistant Exists](why.md) — the problem this project solves
- [Design](design.md) — design principles and decisions
- [Architecture](architecture/overview.md) — how the system is built
- [API](api.md) — endpoint reference
- [Development](development.md) — getting started as a contributor
- [Testing](testing/guide.md) — test guide and philosophy
