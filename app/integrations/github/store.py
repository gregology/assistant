from __future__ import annotations

import logging
from pathlib import Path

from app.store import NoteStore

log = logging.getLogger(__name__)


class PullRequestStore:
    def __init__(self, path: Path) -> None:
        self._store = NoteStore(path)

    @staticmethod
    def _filename(org: str, repo: str, number: int) -> str:
        return f"{org}__{repo}__{number}.md"

    def all(self) -> list[dict]:
        return self._store.all()

    def find(self, org: str, repo: str, number: int) -> Path | None:
        return self._store.find(self._filename(org, repo, number))

    def known_keys(self) -> set[tuple[str, str, int]]:
        return {(pr["org"], pr["repo"], pr["number"]) for pr in self.all()}

    def save(self, pr: dict) -> Path:
        filename = self._filename(pr["org"], pr["repo"], pr["number"])
        return self._store.save(
            filename,
            org=pr["org"],
            repo=pr["repo"],
            number=pr["number"],
            author=pr["author"],
            title=pr["title"],
            status="open",
            draft=pr.get("draft", False),
        )

    def update(self, org: str, repo: str, number: int, **fields) -> Path | None:
        return self._store.update(self._filename(org, repo, number), **fields)

    def archive(self, org: str, repo: str, number: int, **fields) -> Path | None:
        return self._store.archive(self._filename(org, repo, number), **fields)

    def unclassified(self) -> list[dict]:
        return [pr for pr in self.all() if "classification" not in pr]
