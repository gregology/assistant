from __future__ import annotations

import logging

from gaas_sdk import runtime
from gaas_sdk.task import TaskRecord

from .store import IssueStore

log = logging.getLogger(__name__)


def handle(task: TaskRecord):
    from ...client import GitHubClient

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    log.info("github.issues.collect: %s/%s#%d (integration=%s)", org, repo, number, integration_id)

    client = GitHubClient()
    issue = client.get_issue(org, repo, number)
    detail = client.get_issue_detail(org, repo, number)

    store = IssueStore(
        path=runtime.get_notes_dir() / "github" / "issues" / integration.name
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

    runtime.enqueue({
        "type": "github.issues.classify",
        "integration": integration_id,
        "org": org,
        "repo": repo,
        "number": number,
    }, priority=6)
    log.info("github.issues.collect: queued classify for %s/%s#%d", org, repo, number)
