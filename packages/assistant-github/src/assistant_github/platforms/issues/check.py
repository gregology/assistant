from __future__ import annotations

import logging

from assistant_sdk import runtime
from assistant_sdk.task import TaskRecord

from .store import IssueStore

log = logging.getLogger(__name__)


def handle(task: TaskRecord) -> None:
    from ...client import GitHubClient

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    platform = runtime.get_platform(integration_id, "issues")
    log.info("github.issues.check: starting (integration=%s)", integration_id)

    client = GitHubClient()
    store = IssueStore(path=runtime.get_notes_dir() / "github" / "issues" / integration.name)

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
        log.info(
            "Issue **%s/%s#%d** no longer requires attention — moved to synced/",
            org,
            repo,
            number,
        )

    # Enqueue collect for every active issue.
    for issue in remote_issues:
        runtime.enqueue(
            {
                "type": "github.issues.collect",
                "integration": integration_id,
                "org": issue["org"],
                "repo": issue["repo"],
                "number": issue["number"],
            },
            priority=3,
        )

    log.info(
        "github.issues.check: %d active remotely, %d tracked locally, "
        "%d moved to synced/, %d collect tasks queued",
        len(active_remote),
        len(active_local),
        len(stale),
        len(remote_issues),
    )
