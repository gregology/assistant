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
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.issues.collect: %s/%s#%d (integration=%s)", org, repo, number, integration_name)

    client = GitHubClient()
    issue = client.get_issue(org, repo, number)
    detail = client.get_issue_detail(org, repo, number)

    store = IssueStore(
        path=config.directories.notes / "github" / "issues" / integration.name
    )

    if store.find_anywhere(org, repo, number) is not None:
        store.restore_to_active(org, repo, number)
        store.update(
            org, repo, number,
            title=issue["title"],
            state=issue["state"],
            labels=detail["labels"],
            comment_count=detail["comment_count"],
        )
        log.info("github.issues.collect: updated %s/%s#%d", org, repo, number)
    else:
        store.save({
            "org": org,
            "repo": repo,
            "number": number,
            "title": issue["title"],
            "author": issue["author"],
            "state": issue["state"],
            "labels": detail["labels"],
            "comment_count": detail["comment_count"],
        })
        log.info("github.issues.collect: saved new issue %s/%s#%d", org, repo, number)
        log.human("Discovered issue **%s/%s#%d** — %s", org, repo, number, issue["title"])

    queue.enqueue({
        "type": "github.issues.classify",
        "integration": integration_name,
        "org": org,
        "repo": repo,
        "number": number,
    }, priority=6)
    log.info("github.issues.collect: queued classify for %s/%s#%d", org, repo, number)
