"""GitHub App authentication and API helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from githubkit import GitHub, AppAuthStrategy, AppInstallationAuthStrategy


@dataclass
class GitHubContext:
    """Authenticated GitHub client with bot identity and token."""

    gh: GitHub
    bot_name: str
    bot_email: str
    token: str


def build_github_context() -> GitHubContext:
    """Build an authenticated GitHub context from environment variables.

    Requires GITHUB_APP_ID, GITHUB_PRIVATE_KEY, and GITHUB_INSTALLATION_ID.
    GITHUB_PRIVATE_KEY can be a PEM string or a file path.
    """
    app_id = os.environ.get("GITHUB_APP_ID")
    private_key_raw = os.environ.get("GITHUB_PRIVATE_KEY")
    installation_id = os.environ.get("GITHUB_INSTALLATION_ID")

    if not all([app_id, private_key_raw, installation_id]):
        print(
            "Missing environment variables. Set GITHUB_APP_ID, "
            "GITHUB_PRIVATE_KEY, and GITHUB_INSTALLATION_ID.",
            file=sys.stderr,
        )
        sys.exit(1)

    if os.path.isfile(private_key_raw):
        private_key = Path(private_key_raw).read_text()
    else:
        private_key = private_key_raw

    inst_id = int(installation_id)

    app_gh = GitHub(AppAuthStrategy(app_id=app_id, private_key=private_key))

    app_info = app_gh.rest.apps.get_authenticated().parsed_data
    bot_name = f"{app_info.slug}[bot]"
    bot_email = f"{app_info.id}+{app_info.slug}[bot]@users.noreply.github.com"

    token_resp = app_gh.rest.apps.create_installation_access_token(
        installation_id=inst_id,
    )
    token = token_resp.parsed_data.token

    gh = GitHub(AppInstallationAuthStrategy(
        app_id=app_id,
        private_key=private_key,
        installation_id=inst_id,
    ))

    return GitHubContext(gh=gh, bot_name=bot_name, bot_email=bot_email, token=token)


def fetch_issue(gh: GitHub, owner: str, repo: str, number: int) -> dict:
    """Fetch issue metadata."""
    resp = gh.rest.issues.get(owner=owner, repo=repo, issue_number=number)
    issue = resp.parsed_data
    return {
        "number": issue.number,
        "title": issue.title,
        "body": issue.body or "",
        "author": issue.user.login if issue.user else "unknown",
        "state": issue.state,
        "labels": [
            label.name
            for label in (issue.labels or [])
            if isinstance(label, object) and hasattr(label, "name")
        ],
    }


def fetch_comments(gh: GitHub, owner: str, repo: str, number: int) -> list[dict]:
    """Fetch issue comments."""
    resp = gh.rest.issues.list_comments(
        owner=owner, repo=repo, issue_number=number, per_page=100,
    )
    return [
        {
            "author": c.user.login if c.user else "unknown",
            "body": c.body or "",
            "created_at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
        }
        for c in resp.parsed_data
    ]


def post_comment(gh: GitHub, owner: str, repo: str, number: int, body: str) -> str:
    """Post a comment on an issue. Returns the comment URL."""
    resp = gh.rest.issues.create_comment(
        owner=owner, repo=repo, issue_number=number, body=body,
    )
    return resp.parsed_data.html_url


def create_issue(
    gh: GitHub, owner: str, repo: str,
    title: str, body: str, labels: list[str],
) -> str:
    """Create a GitHub issue. Returns the issue URL."""
    resp = gh.rest.issues.create(
        owner=owner, repo=repo,
        title=title, body=body, labels=labels,
    )
    return resp.parsed_data.html_url


def create_pull_request(
    gh: GitHub, owner: str, repo: str, branch: str, issue_number: int,
    pr_body: str, commit_message: str,
) -> str:
    """Create a pull request. Returns the PR URL."""
    body = f"{pr_body}\n\nResolves #{issue_number}"
    resp = gh.rest.pulls.create(
        owner=owner, repo=repo,
        title=commit_message, head=branch, base="main", body=body,
    )
    return resp.parsed_data.html_url
