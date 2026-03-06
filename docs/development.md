# Development

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Installation

Install all dependencies (core packages are installed as editable):

```bash
uv sync
```

## Running the dev server

The supervisor manages both the FastAPI server and the task worker in a single terminal:

```bash
uv run python -m app.supervisor --dev
```

This starts uvicorn with `--reload` (auto-restarts on file changes) and the worker polling loop. The server binds to `127.0.0.1:6767` by default.

Additional flags:

```bash
uv run python -m app.supervisor --dev --expose  # Bind to 0.0.0.0 (LAN access)
uv run python -m app.supervisor --dev --port 8080
```

Or run the server and worker separately if you prefer two terminals:

```bash
uv run fastapi dev             # Server with auto-reload
uv run python -m app.worker    # Task worker
```

## Running tests

```bash
uv run pytest -v
```

This runs tests from six locations (configured in `pyproject.toml`):

- `tests/` - core app tests and safety tests
- `packages/gaas-email/tests/` - email integration tests
- `packages/gaas-gemini/tests/` - Gemini integration tests
- `packages/gaas-github/tests/` - GitHub integration tests
- `packages/gaas-sdk/tests/` - SDK tests (provenance, store, runtime)
- `gaas-bot/tests/` - maintenance bot tests

To run a subset:

```bash
uv run pytest tests/safety               # Safety invariant tests only
uv run pytest packages/gaas-email/tests/  # Email package tests only
```

The test suite uses [Hypothesis](https://hypothesis.readthedocs.io/) for property-based testing of safety invariants. A minimal `config.yaml` is created automatically by `tests/conftest.py` if one doesn't exist.

## Web UI

The UI is a server-rendered interface at `/ui/` built with Jinja2 templates, HTMX, Alpine.js, and DaisyUI. No JavaScript build step. All frontend dependencies load from CDN.

Pages:

- `/ui/` - Dashboard with integration cards, queue stats, and "Run Now" buttons
- `/ui/config` - Full configuration viewer/editor with collapsible sections per integration
- `/ui/queue` - Task queue browser (pending, active, done, failed)
- `/ui/logs` - Audit log browser (daily markdown logs)

The config editor supports editing LLM profiles, scripts, directory paths, and integration-level settings. Changes are written back to `config.yaml` using ruamel.yaml to preserve comments and formatting. After saving, a "Restart Required" banner appears. The supervisor detects a restart sentinel file and restarts both processes automatically.

`!secret` values are displayed as masked placeholders and never resolved in the UI.

See `docs/architecture/web-ui.md` for the full architecture rationale.

## Project layout

Packages live under `packages/` and are installed as editable via `uv sync`. They register themselves as integrations through Python entry points (`gaas.integrations` group), which the app discovers at startup via `app/loader.py`. Each package has its own `pyproject.toml` with dependencies and entry point declarations.
