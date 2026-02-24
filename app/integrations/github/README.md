# GitHub Integration

Tracks pull requests and issues from GitHub repositories. Classifies each item using an LLM and evaluates automation rules against the results.

Requires the [`gh` CLI](https://cli.github.com/) installed and authenticated (`gh auth login`).

## Quick Start

```yaml
integrations:
  - type: github
    name: my_repos
    schedule:
      every: 30m
    platforms:
      pull_requests: {}
      issues: {}
```

That's the minimal config. Both platforms use sensible defaults for classifications. You can enable just one platform if you only care about PRs or issues.

## Configuration Reference

```yaml
integrations:
  - type: github
    name: my_repos                  # Unique name used in logs and note paths
    schedule:
      every: 30m                    # or: cron: "0 8-18 * * 1-5"
    llm: default                    # LLM profile name from the llms: section
    orgs: [myorg]                   # Optional: restrict to specific organizations
    repos: [myorg/myrepo]           # Optional: restrict to specific repositories
    platforms:
      pull_requests:
        include_mentions: false     # Include PRs that mention you (noisy, default: false)
        classifications: ...        # Optional, defaults are used if omitted
      issues:
        include_mentions: true      # Include issues that mention you
        classifications: ...        # Optional, defaults are used if omitted
```

`orgs` and `repos` are shared config at the integration level. They apply to both platforms. `include_mentions` and `classifications` are per-platform.

## Platforms

### Pull Requests

Tracks PRs assigned for review, authored by the user, or (optionally) mentioning the user.

#### Default classifications

| Key | Type | Prompt |
|-----|------|--------|
| `classification.complexity` | confidence | How complex is this PR to review? 0 = trivial typo fix, 1 = major architectural change. |
| `classification.risk` | confidence | How risky is this change to production systems? 0 = no risk, 1 = high risk of breaking things. |
| `classification.documentation_only` | boolean | Is this primarily a documentation or configuration change? |

#### Pipeline

```
github.pull_requests.check (priority 3)
  Searches GitHub for PRs where review is requested, authored by you,
  or mentioning you (if include_mentions is enabled).
  Compares against locally tracked PRs:
    - PRs no longer requiring attention are moved to synced/.
  Enqueues github.pull_requests.collect for every active PR.

github.pull_requests.collect (priority 3)
  Fetches PR metadata (title, status, author, additions, deletions, changed files).
  Creates or updates the PR note in the store.
  If the PR is unclassified, enqueues github.pull_requests.classify.

github.pull_requests.classify (priority 6)
  Fetches the full PR description and diff (truncated to 10,000 characters).
  Renders a classification prompt with salt-based injection defense.
  Calls the LLM and validates structured output.
  Updates the PR note frontmatter with classification results.
  Enqueues github.pull_requests.evaluate.

github.pull_requests.evaluate (priority 7)
  Evaluates automation rules against classification results.
  Enqueues github.pull_requests.act if any automations fire.

github.pull_requests.act (priority 7)
  Stub. Logs only, no actions implemented yet.
```

### Issues

Tracks issues assigned to, authored by, or mentioning the user. Same pipeline shape as pull requests but with issue-specific classifications.

#### Default classifications

| Key | Type | Prompt |
|-----|------|--------|
| `classification.urgency` | confidence | How urgently does this issue need attention? |
| `classification.actionable` | boolean | Can you take a concrete next step on this issue? |

#### Pipeline

```
github.issues.check (priority 3)
  Searches GitHub for issues assigned to you, authored by you,
  or mentioning you (if include_mentions is enabled).
  Filters out pull requests from search results.
  Compares against locally tracked issues:
    - Issues no longer active are moved to synced/.
  Enqueues github.issues.collect for every active issue.

github.issues.collect (priority 3)
  Fetches issue metadata (title, state, author, labels, comment count).
  Creates or updates the issue note in the store.
  If the issue is unclassified, enqueues github.issues.classify.

github.issues.classify (priority 6)
  Fetches the full issue body (truncated to 10,000 characters).
  Renders a classification prompt with salt-based injection defense.
  Calls the LLM and validates structured output.
  Updates the issue note frontmatter with classification results.
  Enqueues github.issues.evaluate.

github.issues.evaluate (priority 7)
  Evaluates automation rules against classification results.
  Enqueues github.issues.act if any automations fire.

github.issues.act (priority 7)
  Stub. Logs only, no actions implemented yet.
```

## Custom Classifications

```yaml
platforms:
  pull_requests:
    classifications:
      # Shorthand: string becomes a confidence classification (0-1)
      complexity: how complex is this PR to review?

      # Boolean
      documentation_only:
        prompt: is this primarily a documentation change?
        type: boolean

      # Enum
      category:
        prompt: what type of change is this?
        type: enum
        values: [feature, bugfix, refactor, docs, chore]

  issues:
    classifications:
      urgency: how urgently does this need attention?
      actionable:
        prompt: can you take a concrete next step?
        type: boolean
```

## Note Store Layout

```
notes/github/pull_requests/{integration-name}/
  myorg__myrepo__42.md     # Active PRs requiring review
  myorg__myrepo__38.md
  synced/
    myorg__myrepo__35.md   # Merged, closed, or no longer assigned

notes/github/issues/{integration-name}/
  myorg__myrepo__101.md    # Active issues
  synced/
    myorg__myrepo__95.md   # Closed or no longer relevant
```

Each note is a markdown file with YAML frontmatter containing metadata and classification results.

## Current Status

Classification and automation evaluation are fully implemented for both platforms. No actions are wired up yet. The `act.py` files are stubs that log only. The `SIMPLE_ACTIONS` sets are empty. Classification results are stored and automation rules are evaluated, but the results don't trigger any external side effects.

The expectation for now is that you set up some rules-based approach in your notes tool to order and prioritize these items. Some automations we may introduce later: creating time-block calendar events for PRs, adding labels to issues, or posting review comments.
