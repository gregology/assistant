from __future__ import annotations

import logging

from gaas_sdk import runtime
from gaas_sdk.task import TaskRecord

from .store import PullRequestStore

log = logging.getLogger(__name__)


def handle(task: TaskRecord) -> None:
    from ...client import GitHubClient

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info(
        "github.pull_requests.collect: %s/%s#%d (integration=%s)",
        org, repo, number, integration_id,
    )

    client = GitHubClient()
    pr = client.get_pr(org, repo, number)
    detail = client.get_pr_detail(org, repo, number)

    store = PullRequestStore(
        path=runtime.get_notes_dir() / "github" / "pull_requests" / integration.name
    )

    if store.find_anywhere(org, repo, number) is not None:
        # If the note was in synced/ (PR requires attention again), restore it.
        store.restore_to_active(org, repo, number)
        store.update(
            org, repo, number,
            title=pr["title"],
            status=pr["status"],
            additions=detail["additions"],
            deletions=detail["deletions"],
            changed_files=detail["changed_files"],
        )
        log.info("github.pull_requests.collect: updated %s/%s#%d", org, repo, number)
    else:
        store.save({
            "org": org,
            "repo": repo,
            "number": number,
            "title": pr["title"],
            "author": pr["author"],
            "additions": detail["additions"],
            "deletions": detail["deletions"],
            "changed_files": detail["changed_files"],
        })
        log.info("github.pull_requests.collect: saved new PR %s/%s#%d", org, repo, number)

    runtime.enqueue({
        "type": "github.pull_requests.classify",
        "integration": integration_id,
        "org": org,
        "repo": repo,
        "number": number,
    }, priority=6)
    log.info("github.pull_requests.collect: queued classify for %s/%s#%d", org, repo, number)
