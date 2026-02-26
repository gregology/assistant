# API Reference

The FastAPI server exposes a small API for inspecting and manually triggering integrations.

## Endpoints

### Health check

```
GET /
```

Returns `{"status": "ok"}`.

### List integrations

```
GET /integrations
```

Returns all configured integrations with their composite ID, type, name, and enabled platforms.

### Trigger an integration

```
POST /integrations/{integration_id}/run
POST /integrations/{integration_id}/{platform}/run
```

Enqueues entry tasks for an integration. The first form fires all enabled platforms. The second targets a specific one.

The `{integration_id}` is a composite ID in `{type}.{name}` format, like `email.personal` or `github.my_repos`. You can grab these from `GET /integrations`.

```bash
# Fire all platforms for the personal email integration
curl -X POST http://localhost:6767/integrations/email.personal/run

# Just the GitHub issues platform
curl -X POST http://localhost:6767/integrations/github.my_repos/issues/run
```

## Scheduled vs manual triggers

No difference in behavior. A manual POST enqueues the same entry tasks that the cron scheduler would. The worker processes both identically, and the downstream task chain (collect, classify, evaluate, act) is the same either way.

Useful for having an external trigger, testing your config, or debugging an integration outside the normal schedule.

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
