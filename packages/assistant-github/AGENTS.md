# assistant-github

The GitHub integration. Handles pull requests and issues via the GitHub REST API using GitHub App credentials.

Discovered at startup via Python entry points. Can be shadowed by a local override during development.

## Structure

```
src/assistant_github/
  __init__.py
  client.py                  # GitHub API client (httpx + GitHub App auth)
  entity_store.py            # GitHubEntityStore base class for PR and issue stores
  manifest.yaml
  platforms/
    pull_requests/
      __init__.py            # Exports HANDLERS dict
      check.py               # Entry task: discover new/updated PRs
      collect.py             # Fetch PR details, diff, comments
      classify.py            # LLM classification
      evaluate.py            # Evaluate automations
      act.py                 # Execute actions (currently no write actions)
      store.py               # PullRequestStore
      const.py               # Safety constants
      templates/
        classify.jinja
    issues/
      __init__.py
      check.py
      collect.py
      classify.py
      evaluate.py
      act.py
      store.py               # IssueStore
      const.py
      templates/
        classify.jinja
  services/
    __init__.py
    create_issue.py          # Chat-proposable service: create GitHub issues
```

## Key patterns

**GitHub App authentication**: `client.py` uses `httpx` to call the GitHub REST API directly. Authentication uses GitHub App credentials: the client generates a JWT (RS256 via PyJWT) from the app's private key, exchanges it for an installation access token, and uses that token for all API requests. The `github_user` config field replaces the `@me` shorthand that the old `gh` CLI approach relied on. Retry with exponential backoff (3 attempts, 1s/2s/4s) is built into the `_request` method.

**GitHubEntityStore**: Base class in `entity_store.py` shared by `PullRequestStore` and `IssueStore`. Provides `find`, `find_anywhere`, `active_keys`, `update`, `move_to_synced`, `restore_to_active` -- all keyed by `(org, repo, number)`. Each subclass overrides only `save()` with entity-specific field mappings.

**Filename convention**: `{org}__{repo}__{number}.md`. Double underscore because org and repo names can contain hyphens.

**Services**: The `create_issue` service is declared in `manifest.yaml` with a `chat` block, which means it's automatically registered as a chat-proposable action at startup. The LLM can propose creating an issue; the user sees a confirmation card and clicks "Post issue" or "Cancel." Approval enqueues a `service.github.create_issue` task through the normal queue. The handler in `services/create_issue.py` calls `GitHubClient.create_issue()`, which POSTs to the GitHub REST API using the App's installation token. Actions taken by the assistant appear under the GitHub App's identity, not the user's.

## Tests

Package-specific tests live in `packages/assistant-github/tests/` and import from `assistant_sdk.*` directly. Safety invariant tests (provenance, automation constants) live in the main `tests/safety/` directory.

No `__init__.py` in the test directory. pytest discovers tests via `--import-mode=importlib`, and skipping `__init__.py` avoids a mypy duplicate-module collision with other packages that also have a `tests/` directory.
