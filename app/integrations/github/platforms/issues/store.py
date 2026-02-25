from __future__ import annotations

from pathlib import Path

from ...entity_store import GitHubEntityStore


class IssueStore(GitHubEntityStore):
    _entity_type = "issue"
    _url_path = "issues"

    def save(self, issue: dict) -> Path:
        org, repo, number = issue["org"], issue["repo"], issue["number"]
        filename = self._filename(org, repo, number)
        return self._store.save(
            filename,
            org=org,
            repo=repo,
            number=number,
            url=f"https://github.com/{org}/{repo}/issues/{number}",
            author=issue.get("author", ""),
            title=issue["title"],
            state=issue.get("state", "open"),
            labels=issue.get("labels", []),
            comment_count=issue.get("comment_count", 0),
        )
