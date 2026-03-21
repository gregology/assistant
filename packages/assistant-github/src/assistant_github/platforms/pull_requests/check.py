from __future__ import annotations

import logging

from assistant_sdk import runtime
from assistant_sdk.task import TaskRecord

from .store import PullRequestStore

log = logging.getLogger(__name__)


def handle(task: TaskRecord) -> None:
    from ...client import GitHubClient

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    platform = runtime.get_platform(integration_id, "pull_requests")
    log.info("github.pull_requests.check: starting (integration=%s)", integration_id)

    client = GitHubClient()
    store = PullRequestStore(
        path=runtime.get_notes_dir() / "github" / "pull_requests" / integration.name
    )

    # Fetch all PRs currently requiring attention from GitHub.
    remote_prs = client.active_prs(integration, platform)
    active_remote: set[tuple[str, str, int]] = {
        (pr["org"], pr["repo"], pr["number"]) for pr in remote_prs
    }

    # PRs currently tracked as active locally (root dir only, excluding synced/).
    active_local = store.active_keys()

    # Notes no longer requiring attention — move to synced/.
    stale = active_local - active_remote
    for org, repo, number in stale:
        store.move_to_synced(org, repo, number)
        log.info(
            "PR **%s/%s#%d** no longer requires attention — moved to synced/",
            org,
            repo,
            number,
        )

    # Enqueue collect for every active PR (upsert: creates new or refreshes metadata).
    for pr in remote_prs:
        runtime.enqueue(
            {
                "type": "github.pull_requests.collect",
                "integration": integration_id,
                "org": pr["org"],
                "repo": pr["repo"],
                "number": pr["number"],
            },
            priority=3,
        )

    log.info(
        "github.pull_requests.check: %d active remotely, %d tracked locally, "
        "%d moved to synced/, %d collect tasks queued",
        len(active_remote),
        len(active_local),
        len(stale),
        len(remote_prs),
    )
