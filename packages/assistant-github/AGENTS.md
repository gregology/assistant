# assistant-github

The GitHub integration. Handles pull requests and issues via the `gh` CLI.

Discovered at startup via Python entry points. Can be shadowed by a local override during development.

## Structure

```
src/assistant_github/
  __init__.py
  client.py                  # GitHub API client (wraps gh CLI)
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
```

## Key patterns

**`gh` CLI as API client**: `client.py` shells out to `subprocess.run(["gh", "api", ...])`. The `gh` CLI handles auth (OAuth device flow, SSH keys, token storage), rate limiting, and pagination. Hard dependency on `gh` being installed and authenticated.

**GitHubEntityStore**: Base class in `entity_store.py` shared by `PullRequestStore` and `IssueStore`. Provides `find`, `find_anywhere`, `active_keys`, `update`, `move_to_synced`, `restore_to_active` -- all keyed by `(org, repo, number)`. Each subclass overrides only `save()` with entity-specific field mappings.

**Filename convention**: `{org}__{repo}__{number}.md`. Double underscore because org and repo names can contain hyphens.

## Tests

Package-specific tests live in `packages/assistant-github/tests/` and import from `assistant_sdk.*` directly. Safety invariant tests (provenance, automation constants) live in the main `tests/safety/` directory.

No `__init__.py` in the test directory. pytest discovers tests via `--import-mode=importlib`, and skipping `__init__.py` avoids a mypy duplicate-module collision with other packages that also have a `tests/` directory.
