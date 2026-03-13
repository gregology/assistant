# Recipe: Service Integration

Step-by-step guide for building a service integration. A service is a callable function that other integrations invoke from automation rules. No polling, no events, no schedule.

Read `contract.md` and `safety.md` first.

We'll use a fictional "Todoist task creation" service as the running example.

## Step 1: Create the package skeleton

Services are simpler than platforms. No `const.py`, no snapshot, no pipeline.

```
packages/assistant-todoist/
  pyproject.toml
  src/assistant_todoist/
    __init__.py
    manifest.yaml
    client.py
    services/
      __init__.py
      create_task.py
  tests/
    test_create_task.py
```

## Step 2: Write pyproject.toml

```toml
[project]
name = "assistant-todoist"
version = "0.1.0"
description = "Todoist integration for Assistant"
requires-python = ">=3.11"
dependencies = [
    "assistant-sdk>=0.1.0",
    "todoist-api-python>=2.0",
]

[project.entry-points."assistant.integrations"]
todoist = "assistant_todoist"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/assistant_todoist"]
```

Update the root `pyproject.toml` the same way as platform integrations.

## Step 3: Write manifest.yaml

The key difference: `platforms: {}` and a `services:` block.

```yaml
domain: todoist
name: "Todoist"
version: "0.1.0"
entry_task: ""
dependencies:
  - todoist-api-python
config_schema:
  properties:
    api_token:
      type: string
    default_project:
      type: string
      default: "Inbox"
  required:
    - api_token
platforms: {}
services:
  create_task:
    name: "Create Task"
    description: "Creates a task in Todoist"
    handler: ".services.create_task.handle"
    reversible: false
    human_log: "Created Todoist task: {{ title | truncate(60) }}"
    input_schema:
      properties:
        title:
          type: string
        description:
          type: string
        priority:
          type: integer
        project:
          type: string
      required:
        - title
```

### Reversibility decision

This service creates tasks in an external system. That's irreversible -- you can't un-create a task in Todoist from Assistant (you'd have to delete it, which is a separate action). So `reversible: false` is correct.

A service that only reads from an external API is still irreversible if it sends user-context data. The Gemini web research service is irreversible because the search query might contain private information extracted from emails. You can't un-send the network request.

The only services that can be `reversible: true` are ones that:
- Only access local data (filesystem, local database)
- Don't transmit anything externally

Leave the default (`false`) unless you're absolutely sure.

## Step 4: Write the client

```python
from todoist_api import TodoistAPI


class TodoistClient:
    def __init__(self, api_token: str):
        self._api = TodoistAPI(api_token)

    def create_task(
        self,
        title: str,
        description: str = "",
        priority: int = 1,
        project: str | None = None,
    ) -> dict:
        task = self._api.add_task(
            content=title,
            description=description,
            priority=priority,
            project_name=project,
        )
        return {
            "id": task.id,
            "url": task.url,
            "title": task.content,
        }
```

## Step 5: Write the service handler

Service handlers receive the full task dict and return a result dict. The worker routes the result based on `on_result`.

```python
import logging
from assistant_sdk import runtime
from assistant_todoist.client import TodoistClient

log = logging.getLogger(__name__)


def handle(task: dict) -> dict:
    payload = task["payload"]
    integration_id = payload.get("integration", "")
    inputs = payload.get("inputs", {})

    title = inputs.get("title", "")
    if not title:
        log.warning("create_task called with empty title, skipping")
        return {"error": "empty title"}

    cfg = runtime.get_integration(integration_id)
    client = TodoistClient(api_token=cfg.api_token)

    project = inputs.get("project") or getattr(cfg, "default_project", "Inbox")

    result = client.create_task(
        title=title,
        description=inputs.get("description", ""),
        priority=inputs.get("priority", 1),
        project=project,
    )

    log.info("Created Todoist task: %s (id=%s)", title[:60], result["id"])
    return result
```

The returned dict gets saved as a markdown note by default. The `id`, `url`, and `title` keys become frontmatter fields.

## Step 6: Write tests

Mock the external API. Test the handler with different input combinations.

```python
from unittest.mock import MagicMock, patch


def test_create_task_returns_result():
    mock_client = MagicMock()
    mock_client.create_task.return_value = {
        "id": "123",
        "url": "https://todoist.com/task/123",
        "title": "Test task",
    }

    task = {
        "payload": {
            "type": "service.todoist.create_task",
            "integration": "todoist.default",
            "inputs": {
                "title": "Test task",
                "priority": 2,
            },
        }
    }

    with patch("assistant_todoist.services.create_task.TodoistClient", return_value=mock_client):
        with patch("assistant_todoist.services.create_task.runtime") as mock_runtime:
            mock_runtime.get_integration.return_value = MagicMock(
                api_token="fake", default_project="Inbox",
            )
            from assistant_todoist.services.create_task import handle
            result = handle(task)

    assert result["id"] == "123"
    assert result["title"] == "Test task"
    mock_client.create_task.assert_called_once()


def test_empty_title_skips():
    task = {
        "payload": {
            "type": "service.todoist.create_task",
            "integration": "todoist.default",
            "inputs": {"title": ""},
        }
    }

    with patch("assistant_todoist.services.create_task.runtime"):
        from assistant_todoist.services.create_task import handle
        result = handle(task)

    assert "error" in result
```

## Step 7: How users invoke it

Services are triggered from automation `then` clauses in other integrations' configs. A user might set up their email integration to create Todoist tasks for high-priority emails:

```yaml
integrations:
  - type: email
    name: personal
    # ... email config ...
    platforms:
      inbox:
        automations:
          - when:
              classification.priority: "high"
              classification.actionable: true
            then:
              - !yolo
                service:
                  call: todoist.default.create_task
                  inputs:
                    title: "Reply to {{ from_address }}: {{ subject }}"
                    description: "Email requires response"
                    priority: 3

  - type: todoist
    name: default
    api_token: !secret todoist_api_token
```

The `!yolo` is required because:
1. The `when` clause has `classification.*` conditions -> LLM provenance
2. The todoist service is irreversible (`reversible: false`)

Without `!yolo`, the safety validation would disable this automation at config load time.

### The call format

`call: todoist.default.create_task` breaks down as:
- `todoist` -- the integration type (domain)
- `default` -- the integration instance name
- `create_task` -- the service name from your manifest

### Overriding result routing

By default, service results get saved as notes in `{notes_dir}/services/todoist/create_task/`. Users can customize this:

```yaml
then:
  - !yolo
    service:
      call: todoist.default.create_task
      inputs:
        title: "Reply to {{ from_address }}"
      on_result:
        - type: note
          path: tasks/email_followups/
```

### Overriding human_log

The `human_log` template from your manifest is used by default. Users can override it per-automation:

```yaml
then:
  - !yolo
    service:
      call: todoist.default.create_task
      inputs:
        title: "{{ subject }}"
      human_log: "Todoist: created task for email from {{ from_address }}"
```

The rendered string shows up in the daily audit log at `logs/YYYY-MM-DD DayOfWeek.md`.

## Adding more services

If your integration offers multiple services, add them to the manifest and create separate handler modules:

```yaml
services:
  create_task:
    handler: ".services.create_task.handle"
    # ...
  complete_task:
    handler: ".services.complete_task.handle"
    reversible: false
    input_schema:
      properties:
        task_id: { type: string }
      required: [task_id]
```

Each service gets its own task type (`service.todoist.create_task`, `service.todoist.complete_task`), its own input schema, and its own reversibility classification.

## Hybrid integrations

If your integration needs both platform polling and callable services, you can have both. Set `platforms:` with your platform definitions and `services:` with your service definitions. They're independent -- the platform pipeline and the service handlers don't interact unless the platform's automations trigger the services.

This would be unusual. Most integrations are one or the other. But the architecture supports it if you need it.
