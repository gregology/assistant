from __future__ import annotations

import logging

from app import queue
from app.config import config
from ...client import GitHubClient
from .store import PullRequestStore

log = logging.getLogger(__name__)


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name, "github")
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.pull_requests.collect: %s/%s#%d (integration=%s)", org, repo, number, integration_name)

    client = GitHubClient()
    pr = client.get_pr(org, repo, number)
    detail = client.get_pr_detail(org, repo, number)

    store = PullRequestStore(
        path=config.directories.notes / "github" / "pull_requests" / integration.name
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
        log.human("Discovered PR **%s/%s#%d** — %s", org, repo, number, pr["title"])

    queue.enqueue({
        "type": "github.pull_requests.classify",
        "integration": integration_name,
        "org": org,
        "repo": repo,
        "number": number,
    }, priority=6)
    log.info("github.pull_requests.collect: queued classify for %s/%s#%d", org, repo, number)
