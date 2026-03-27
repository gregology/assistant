# assistant-github

Monitors GitHub pull requests and issues via the GitHub REST API. Authenticates as a GitHub App so actions taken by the assistant (e.g., creating issues) appear under the App's identity, not your personal account. Classifies items with an LLM and tracks them as markdown notes with YAML frontmatter. Currently read-only with no write actions except issue creation.

## Prerequisites

- A [GitHub App](https://docs.github.com/en/apps/creating-github-apps) installed on the repos/orgs you want to monitor
- The App's **App ID**, **Installation ID**, and **private key** (`.pem` file)

Run `assistant setup` and follow the prompts to configure credentials, or add them manually to your config and secrets files (see below).

## Config

```yaml
integrations:
  - type: github
    name: my_repos
    github_user: your-github-username
    app_id: !secret github_app_id
    installation_id: !secret github_installation_id
    private_key: !secret github_private_key
    schedule:
      every: 30m
    llm: default
    # orgs: [myorg]                  # Restrict to specific orgs
    # repos: [myorg/myrepo]          # Restrict to specific repos (org/repo format)
    # repos:                         # Or use object form to add context for the LLM:
    #   - repo: myorg/backend
    #     context: "Python API server. Issues should include endpoint and error details."
    #   - myorg/docs                 # Plain strings still work alongside object entries
    platforms:
      pull_requests:
        # include_mentions: true     # Also track PRs that mention you (off by default)
        classifications:
          # ...
      issues:
        include_mentions: true
        classifications:
          # ...
```

`github_user` is the GitHub username whose activity should be monitored (replaces the `@me` shorthand used internally).

Both `orgs` and `repos` are optional. Leave them out and the integration discovers repos from your GitHub activity. Use `repos` for a specific list in `org/repo` format, or `orgs` to track everything in an organization. Repo entries can be plain strings or objects with `repo` and `context` fields — the `context` is included in the chat system prompt so the LLM knows which repo to target and what details to include when proposing issues.

Credentials go in `secrets.yaml`:

```yaml
github_app_id: "123456"
github_installation_id: "78901234"
github_private_key: |
  -----BEGIN RSA PRIVATE KEY-----
  ...
  -----END RSA PRIVATE KEY-----
```

## Platforms

### pull_requests

Discovers and classifies pull requests you authored or were requested to review.

**Default classifications:**

| Name | Type | Prompt |
|------|------|--------|
| `complexity` | confidence | How complex is this pull request to review? 0 = trivial typo fix, 1 = major architectural change. |
| `risk` | confidence | How risky is this change to production systems? 0 = no risk, 1 = high risk of breaking things. |
| `documentation_only` | boolean | Is this primarily a documentation or configuration change? |

**Deterministic sources** for `when` conditions: `org`, `repo`, `number`, `author`, `title`, `status`, `additions`, `deletions`, `changed_files`.

### issues

Discovers and classifies issues assigned to you or that mention you.

**Default classifications:**

| Name | Type | Prompt |
|------|------|--------|
| `urgency` | confidence | How urgently does this issue need attention? 0 = no urgency, 1 = critical. |
| `actionable` | boolean | Can you take a concrete next step on this issue right now? |

**Deterministic sources** for `when` conditions: `org`, `repo`, `number`, `author`, `title`, `state`, `labels`, `comment_count`.

## Automations

Both platforms support automations, but there are no write actions yet. For now automations are limited to cross-cutting actions like scripts and services.

```yaml
platforms:
  pull_requests:
    automations:
      - when:
          classification.risk: "> 0.8"
        then:
          - script:
              name: notify_slack
              inputs:
                message: "High-risk PR needs review: $title"
```

When write actions land (comment, approve, request changes, etc.) they'll go through the same reversibility review that email actions did.

## Notes

PRs and issues are stored as markdown files with YAML frontmatter in your notes directory. Filenames use `{org}__{repo}__{number}.md` with double underscores because org and repo names can contain hyphens.
