# GaaS
Greg-as-a-Service, a developer

## Setup

```bash
uv sync
```

## Run

Development server (auto-reload, localhost only):

```bash
uv run fastapi dev
```

Production server:

```bash
uv run fastapi run
```

API docs available at http://127.0.0.1:8000/docs

## Worker

The task queue worker polls for pending tasks and processes them. Run it in a separate terminal alongside the API server:

```bash
uv run python -m app.worker
```
