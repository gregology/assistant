from __future__ import annotations

import logging

import app.human_log  # noqa: F401 — registers log.human()
from app import queue
from app.config import config
from .client import GitHubClient
from .store import PullRequestStore

log = logging.getLogger(__name__)


def _pr_path():
    return config.directories.notes / "github" / "pull_requests"


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    log.info("github.update_prs: starting (integration=%s)", integration_name)
    client = GitHubClient()
    store = PullRequestStore(path=_pr_path())

    _refresh_existing(store, client)
    _discover_new(store, client)

    for pr in store.unclassified():
        queue.enqueue({
            "type": "github.classify_pr",
            "integration": integration_name,
            "org": pr["org"],
            "repo": pr["repo"],
            "number": pr["number"],
        }, priority=6)
        log.info("github.update_prs: queued classify for %s/%s#%d",
                 pr["org"], pr["repo"], pr["number"])

    log.info("github.update_prs: done")


def _refresh_existing(store: PullRequestStore, client: GitHubClient) -> None:
    existing = store.all()
    log.info("github.update_prs: refreshing %d existing PRs", len(existing))

    for pr in existing:
        org, repo, number = pr["org"], pr["repo"], pr["number"]
        try:
            current = client.get_pr(org, repo, number)
        except Exception:
            log.exception("github.update_prs: failed to fetch %s/%s#%d", org, repo, number)
            continue

        if current["status"] in ("closed", "merged"):
            store.archive(org, repo, number, status=current["status"])
            log.info("github.update_prs: archived %s/%s#%d (%s)",
                     org, repo, number, current["status"])
            log.human("Archived PR %s/%s#%d (%s)", org, repo, number, current["status"])
        else:
            store.update(org, repo, number,
                         title=current["title"], status=current["status"],
                         draft=current["draft"])


def _discover_new(store: PullRequestStore, client: GitHubClient) -> None:
    known = store.known_keys()
    assigned = client.assigned_prs()
    log.info("github.update_prs: found %d assigned PRs via API", len(assigned))

    new_count = 0
    for pr in assigned:
        key = (pr["org"], pr["repo"], pr["number"])
        if key not in known:
            store.save(pr)
            new_count += 1
            log.info("github.update_prs: saved new PR %s/%s#%d",
                     pr["org"], pr["repo"], pr["number"])
            log.human("New PR to review: %s/%s#%d — %s",
                      pr["org"], pr["repo"], pr["number"], pr["title"])

    log.info("github.update_prs: created %d new PR files", new_count)
