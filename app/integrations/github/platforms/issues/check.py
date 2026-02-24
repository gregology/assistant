from __future__ import annotations

import logging

from app import queue
from app.config import config
from ...client import GitHubClient
from .store import IssueStore

log = logging.getLogger(__name__)


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name, "github")
    platform = config.get_platform(integration_name, "github", "issues")
    log.info("github.issues.check: starting (integration=%s)", integration_name)

    client = GitHubClient()
    store = IssueStore(
        path=config.directories.notes / "github" / "issues" / integration.name
    )

    # Fetch all issues currently requiring attention from GitHub.
    remote_issues = client.active_issues(integration, platform)
    active_remote: set[tuple[str, str, int]] = {
        (issue["org"], issue["repo"], issue["number"]) for issue in remote_issues
    }

    # Issues currently tracked as active locally (root dir only, excluding synced/).
    active_local = store.active_keys()

    # Notes no longer requiring attention — move to synced/.
    stale = active_local - active_remote
    for org, repo, number in stale:
        store.move_to_synced(org, repo, number)
        log.human("Issue **%s/%s#%d** no longer requires attention — moved to synced/", org, repo, number)

    # Enqueue collect for every active issue.
    for issue in remote_issues:
        queue.enqueue({
            "type": "github.issues.collect",
            "integration": integration_name,
            "org": issue["org"],
            "repo": issue["repo"],
            "number": issue["number"],
        }, priority=3)

    log.info(
        "github.issues.check: %d active remotely, %d tracked locally, %d moved to synced/, %d collect tasks queued",
        len(active_remote), len(active_local), len(stale), len(remote_issues),
    )
