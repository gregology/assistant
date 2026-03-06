# GaaS (Greg as a Service)

An AI-powered personal assistant that processes emails, pull requests, issues, and other inputs using LLMs, safely. Think of it as OpenClaw but with elastic bands around the pinchers: same category of tool, but with safety and auditability as first-class concerns rather than afterthoughts.

## Core Principles

These are non-negotiable. Every feature, integration, and code change must respect them.

### Principle of Reversibility

Every autonomous action must be reversible. Draft instead of send. Archive instead of delete. Search local notes instead of sending data to Google. If an action cannot be undone, it requires human-in-the-loop approval.

### Principle of Audibility

Every action the AI takes must be auditable. Log what the agent does, don't ask it what it did. The filesystem is the database: task queues are YAML files you can inspect, notes are markdown with frontmatter, and daily logs are human-readable markdown.

### Principle of Accountability

AI has ability but no accountability. Every non-reversible action requires a human in the loop. The LLM classifies. The deterministic dispatch layer decides. The dispatch layer is the safety boundary, not the LLM.

## Design Principles

- **WWHAD (What Would Home Assistant Do?)** - HA has battle-hardened patterns for complex config and intuitive UIs. Follow those patterns for YAML configuration and the eventual goal of non-developer-friendly tooling.
- **Default to code** - Don't burn tokens asking an LLM to do a programmable task. If it can be a `if/else`, it should be.
- **Zero trust** - Never rely on LLM output being correct. The deterministic dispatch layer enforces safety, not the prompt.
- **Human readable** - Every "decision" leaves a human-readable audit trail. (LLMs don't make decisions, they make next-token predictions.)
- **Optimize for memory** - RAM is for inference. Disk-based queues and markdown stores keep the memory footprint low.
- **Backend-agnostic LLMs** - Users choose their own LLM backend (Ollama, OpenAI-compatible, etc.). Local inference is the default.

## Provenance Tracking

When an action is triggered by LLM classification vs. deterministic rules (e.g. domain-based filtering), the system tracks the provenance. Every automation rule is assigned `rule`, `llm`, or `hybrid` provenance based on its condition keys. Irreversible actions are blocked from `llm` and `hybrid` provenance unless explicitly overridden with the `!yolo` tag. See `packages/gaas-email/AGENTS.md` for the email provenance model, and `tests/safety/test_provenance.py` for the safety tests that enforce it.

## Tech Stack

- **Python 3.12+**, managed with `uv`
- **FastAPI** with cron scheduling (`fastapi-crons`)
- **Filesystem-based task queue** using YAML files in `pending/`, `active/`, `done/`, `failed/`
- **Markdown + YAML frontmatter** for all persistent state (`python-frontmatter`)
- **Jinja2** for prompt templates with injection defenses
- **Hypothesis** for property-based testing of safety invariants
- **httpx** as HTTP client for LLM backends
- **gaas-sdk** contracts package for models, evaluation engine, classification, NoteStore, and runtime registration
- Integration-specific dependencies live in their packages (`imap-tools` in gaas-email, `google-genai` in gaas-gemini, etc.)

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/gregology/GaaS/main/install.sh | bash
```

This installs GaaS to `~/.gaas`, creates a `gaas` CLI wrapper in `~/.local/bin`, and offers to run the setup wizard. Re-running the same command on an existing install triggers `gaas update`.

Environment variables for customization:
- `GAAS_HOME` — install directory (default: `~/.gaas`)
- `GAAS_BIN_DIR` — CLI wrapper location (default: `~/.local/bin`)
- `GAAS_BRANCH` — branch to track (default: `main`)

## CLI

After installation, `gaas` is the main entry point:

```bash
gaas start [--dev] [--expose] [--port N]  # Start server + worker
gaas setup [--reconfigure]                # Guided config wizard
gaas update                               # Update to latest version
gaas doctor                               # Diagnostic checks
gaas version                              # Version info
gaas status                               # Check if running
gaas logs [--tail N]                      # Show human-readable audit logs
```

## Development

```bash
uv sync                              # Install dependencies
uv run python -m app.supervisor --dev # Dev mode: server + worker (single terminal)
uv run pytest -v                      # Run tests
```

The supervisor accepts additional flags:

```bash
uv run python -m app.supervisor --expose        # Allow external connections (bind 0.0.0.0)
uv run python -m app.supervisor --port 8080     # Custom port (default: 6767)
uv run python -m app.supervisor --dev --expose  # Combine flags as needed
```

By default the server binds to `127.0.0.1` (localhost only). Use `--expose` to bind to `0.0.0.0` and accept connections from other devices on the network.

Or run server and worker separately:

```bash
uv run fastapi dev             # Dev server (auto-reload)
uv run python -m app.worker    # Task worker (separate terminal)
```

For development you can also use the CLI directly without the wrapper:

```bash
uv run python -m app.cli <subcommand>
```

## Project Structure

```
install.sh         # Curl-pipe-bash installer/updater (thin bootstrapper)
packages/
  gaas-sdk/        # Contracts layer: models, evaluate, classify, NoteStore, runtime
  gaas-email/      # Email integration (inbox platform, IMAP)
  gaas-github/     # GitHub integration (pull_requests + issues platforms)
  gaas-gemini/     # Gemini integration (web_research service)
app/
  cli.py           # CLI entry point: gaas start|setup|update|doctor|version|status|logs
  setup.py         # Guided setup wizard (generates config.yaml + secrets.yaml)
  doctor.py        # Diagnostic checks (prereqs, config, connectivity)
  main.py          # FastAPI server, endpoints, scheduler init
  runtime_init.py  # Registers app implementations with gaas_sdk.runtime
  config.py        # YAML config + !secret references, dynamic Pydantic models
  loader.py        # Integration discovery (builtin dir, custom dir, entry points)
  queue.py         # Filesystem-based task queue
  queue_policy.py  # Config-driven dedup + rate limiting (wraps queue.enqueue)
  worker.py        # Task worker polling loop, result capture + routing
  result_routes.py # Service result routing (note persistence, human log breadcrumb)
  supervisor.py    # Process supervisor (manages server + worker)
  scheduler.py     # Cron scheduling from config
  llm.py           # LLM backend abstraction, structured output, retry
  store.py         # Re-exports NoteStore from gaas_sdk.store
  evaluate.py      # Re-exports from gaas_sdk.evaluate
  classify.py      # Re-exports from gaas_sdk.classify
  human_log.py     # Human-readable daily markdown logs
  actions/         # Re-exports from gaas_sdk.actions + script executor
  integrations/    # Handler registry, entry-point loader, custom integration support
tests/
  test_cli.py      # CLI, setup wizard, and doctor tests
  safety/          # Property-based and chaos tests for safety invariants
```

Integrations ship as installable packages under `packages/` and are discovered via Python entry points at startup. Custom integrations live in a user-configurable directory (set via `directories.custom_integrations` in `config.yaml`). See `app/integrations/AGENTS.md` for the integration package contract.

## Configuration

Config uses Home Assistant-inspired YAML with `!secret` references to a separate `secrets.yaml`. See `example.config.yaml` for a starter config and the integration READMEs (`packages/gaas-*/README.md`) for full config references. Key patterns:

- **Classification shorthand**: `human: "is this a personal email?"` expands to `{prompt: "...", type: "confidence"}`
- **Automation rules**: `when`/`then` pairs with operators (`>`, `>=`, `<`, `<=`, `==`) for confidence, exact/list match for enums, identity for booleans
- **Platform-level config**: classifications and automations live under `platforms:` within each integration
- **Schedule formats**: `every: 30m` or `cron: "0 8-18 * * 1-5"`
- **Multiple LLM profiles**: Define `default`, `fast`, etc. with different backends/models
- **Scripts**: User-defined shell scripts in `scripts:` section, triggered from automation rules as a cross-cutting action type
- **Queue policies**: `queue_policies:` section with `defaults` and per-type `overrides` for dedup and rate limiting

Both `config.yaml` and `secrets.yaml` are gitignored. Tests create a minimal config automatically via `conftest.py`.

## Safety-Critical Patterns

1. **LLM output is untrusted input.** Schema validation with retries, but the dispatch layer enforces what actions are possible, not the LLM.
2. **Prompt injection defense is dual-barrier.** Random salt markers isolate untrusted content. The deterministic dispatch layer prevents unsafe actions even if the prompt barrier fails.
3. **Test rigor is proportional to irreversibility**, not code complexity. See `tests/AGENTS.md`.
4. **Unknown actions are rejected.** `act.py` has an explicit allowlist (`SIMPLE_ACTIONS`). Unknown string or dict actions are skipped with a warning.
5. **The `SIMPLE_ACTIONS` set must not grow without deliberate reversibility review.** Adding a new action requires classifying it by reversibility tier first.
6. **Scripts are irreversible by default.** The system can't statically verify what shell code does. `reversible: true` is an explicit opt-in on the script definition. Without it, script actions are blocked from `llm`/`hybrid` provenance unless wrapped in `!yolo`.
7. **Services are irreversible by default.** Same as scripts. The manifest can declare `reversible: true`, but only for services that are both read-only **and** do not transmit data beyond the system boundary. "Read-only" is necessary but not sufficient -- a service that sends user-context data to an external API (like Gemini web research) is irreversible because you cannot un-send that data. Safety validation enforces `!yolo` for irreversible services from LLM provenance.

## Code Quality Tools

Dev dependencies for spotting tech debt, dead code, complexity creep, and architectural violations. See `docs/code-quality-tools.md` for full usage details. Quick reference:

```bash
uv run mypy app/ packages/ --ignore-missing-imports   # Type checking
uv run complexipy app/ packages/ --max-complexity 15   # Cognitive complexity
uv run radon cc app/ -a -nc                            # Cyclomatic complexity (C+ only)
uv run vulture app/ packages/ --min-confidence 80      # Dead code
uv run deptry .                                        # Unused/missing deps
uv run bandit -r app/ -q                               # Security issues
uv run ruff check app/ packages/ tests/                # Linting
uv run lint-imports                                    # Architectural boundary checks
uv run pytest --cov=app --cov-report=term-missing -v   # Test coverage
```

When to use these: before refactors, after extracting or removing code, or as periodic sweeps. Not every commit. The output is signal for human judgment, not a checklist.

Key tools for this codebase:
- **import-linter** enforces the SDK boundary (integrations must not import `app.*`). Config lives in `pyproject.toml` under `[tool.importlinter]`.
- **bandit** will always flag `app/actions/script.py` because it runs shell commands by design. Suppress reviewed findings with `# nosec`.
- **vulture** can't see dynamic handler registration (`HANDLERS` dicts, entry points), so expect false positives there.
- **complexipy** — `config.py` and `loader.py` are the usual complexity hotspots. Functions above 15 are worth looking at.

## Adding New Code

- Categorize every new action by reversibility tier before writing tests
- Follow the existing Pydantic model patterns for config validation
- Use `log.human()` for actions that should appear in the daily audit log
- Use `log.info()` for operational logging
- Prefer filesystem state over in-memory state
- Never send user data to external services without explicit user configuration and awareness
