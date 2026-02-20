from __future__ import annotations

import logging

from app import queue
from app.config import config
from .client import GitHubClient
from .store import PullRequestStore

log = logging.getLogger(__name__)


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name, "github")
    log.info("github.check: starting (integration=%s)", integration_name)

    client = GitHubClient()
    store = PullRequestStore(
        path=config.directories.notes / "github" / "pull_requests" / integration.name
    )

    # Fetch all PRs currently requiring attention from GitHub.
    remote_prs = client.active_prs(integration)
    active_remote: set[tuple[str, str, int]] = {
        (pr["org"], pr["repo"], pr["number"]) for pr in remote_prs
    }

    # PRs currently tracked as active locally (root dir only, excluding synced/).
    active_local = store.active_keys()

    # Notes no longer requiring attention — move to synced/.
    stale = active_local - active_remote
    for org, repo, number in stale:
        store.move_to_synced(org, repo, number)
        log.human("PR **%s/%s#%d** no longer requires attention — moved to synced/", org, repo, number)

    # Enqueue collect for every active PR (upsert: creates new or refreshes metadata).
    for pr in remote_prs:
        queue.enqueue({
            "type": "github.collect",
            "integration": integration_name,
            "org": pr["org"],
            "repo": pr["repo"],
            "number": pr["number"],
        }, priority=3)

    log.info(
        "github.check: %d active remotely, %d tracked locally, %d moved to synced/, %d collect tasks queued",
        len(active_remote), len(active_local), len(stale), len(remote_prs),
    )
