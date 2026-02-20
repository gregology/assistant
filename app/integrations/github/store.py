from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from app.store import NoteStore

log = logging.getLogger(__name__)


class PullRequestStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._store = NoteStore(path)

    @staticmethod
    def _filename(org: str, repo: str, number: int) -> str:
        return f"{org}__{repo}__{number}.md"

    def all(self) -> list[dict]:
        return self._store.all()

    def find(self, org: str, repo: str, number: int) -> Path | None:
        """Find a PR note in the root (active) directory."""
        return self._store.find(self._filename(org, repo, number))

    def find_anywhere(self, org: str, repo: str, number: int) -> Path | None:
        """Find a PR note in the root directory or synced/ subdirectory."""
        path = self.find(org, repo, number)
        if path:
            return path
        synced = self._path / "synced" / self._filename(org, repo, number)
        return synced if synced.exists() else None

    def active_keys(self) -> set[tuple[str, str, int]]:
        """Return (org, repo, number) tuples for PRs in the root directory only.

        PRs in synced/ are excluded — they no longer require attention.
        """
        if not self._path.is_dir():
            return set()
        keys: set[tuple[str, str, int]] = set()
        for f in self._path.glob("*.md"):
            try:
                post = frontmatter.load(f)
                meta = post.metadata
                org = meta.get("org")
                repo = meta.get("repo")
                number = meta.get("number")
                if org and repo and number is not None:
                    keys.add((org, repo, int(number)))
            except Exception:
                log.warning("Failed to parse front matter: %s", f)
        return keys

    def save(self, pr: dict) -> Path:
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

    def update(self, org: str, repo: str, number: int, **fields) -> Path | None:
        return self._store.update(self._filename(org, repo, number), **fields)

    def move_to_synced(self, org: str, repo: str, number: int, **fields) -> Path | None:
        """Move a PR note from the root to synced/ (no longer requires attention)."""
        filename = self._filename(org, repo, number)
        note_path = self._store.find(filename)
        if note_path is None:
            log.warning("Cannot move to synced/: note not found for %s/%s#%d", org, repo, number)
            return None
        if fields:
            post = frontmatter.load(note_path)
            for key, value in fields.items():
                post[key] = value
            note_path.write_text(frontmatter.dumps(post))
        dest_dir = self._path / "synced"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename
        note_path.rename(dest_path)
        log.info("Moved PR %s/%s#%d to synced/", org, repo, number)
        return dest_path

    def restore_to_active(self, org: str, repo: str, number: int) -> Path | None:
        """Move a PR note from synced/ back to root (PR requires attention again)."""
        synced_path = self._path / "synced" / self._filename(org, repo, number)
        if not synced_path.exists():
            return None
        self._path.mkdir(parents=True, exist_ok=True)
        dest = self._path / self._filename(org, repo, number)
        synced_path.rename(dest)
        log.info("Restored PR %s/%s#%d from synced/ to active", org, repo, number)
        return dest
