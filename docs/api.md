# API Reference

The FastAPI server exposes a small API for inspecting and manually triggering integrations.

## Endpoints

### Health check

```
GET /
```

Returns a basic health check response.

### List integrations

```
GET /integrations
```

Returns all configured integrations with their type, name, and enabled platforms.

### Trigger an integration

```
POST /integrations/{type}/{name}/run
POST /integrations/{type}/{name}/run?platform=pull_requests
```

Manually triggers an integration's entry tasks. Without the `platform` query parameter, this enqueues entry tasks for all enabled platforms. With `platform`, only the specified platform is triggered.

The `{type}` parameter matches the integration's `type` field (e.g. `email`, `github`). The `{name}` parameter matches the `name` field from your `config.yaml` integration entry.

Examples:

```bash
# Trigger all platforms for the personal email integration
curl -X POST http://localhost:8000/integrations/email/personal/run

# Trigger all platforms for the GitHub integration
curl -X POST http://localhost:8000/integrations/github/my_repos/run

# Trigger only the issues platform
curl -X POST http://localhost:8000/integrations/github/my_repos/run?platform=issues
```

## Scheduled vs manual triggers

There is no difference in behavior. A manual POST enqueues the same entry tasks that the cron scheduler enqueues automatically. The worker processes both identically. Downstream task chains (collect, classify, evaluate, act) are the same regardless of how the entry task was created.

Manual triggers are useful for testing your config, debugging an integration, or running a one-off check outside the normal schedule.

## Running the server

Development server with auto-reload:

```bash
uv run fastapi dev
```

Production server:

```bash
uv run fastapi run
```

The worker must run in a separate terminal for tasks to actually be processed:

```bash
uv run python -m app.worker
```
