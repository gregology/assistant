from __future__ import annotations

from pathlib import Path
from typing import Any

from ...entity_store import GitHubEntityStore


class PullRequestStore(GitHubEntityStore):
    _entity_type = "PR"
    _url_path = "pull"

    def save(self, pr: dict[str, Any]) -> Path:
        org, repo, number = pr["org"], pr["repo"], pr["number"]
        filename = self._filename(org, repo, number)
        return self._store.save(
            filename,
            org=org,
            repo=repo,
            number=number,
            url=f"https://github.com/{org}/{repo}/pull/{number}",
            author=pr.get("author", ""),
            title=pr["title"],
            status="open",
            additions=pr.get("additions", 0),
            deletions=pr.get("deletions", 0),
            changed_files=pr.get("changed_files", 0),
        )
