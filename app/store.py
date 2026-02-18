from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

log = logging.getLogger(__name__)


class NoteStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._archive = path / "archive"

    def all(self) -> list[dict]:
        if not self._path.is_dir():
            return []
        items = []
        for f in self._path.glob("*.md"):
            try:
                post = frontmatter.load(f)
                items.append(dict(post.metadata))
            except Exception:
                log.warning("Failed to parse front matter: %s", f)
        return items

    def find(self, filename: str) -> Path | None:
        path = self._path / filename
        return path if path.exists() else None

    def save(self, filename: str, content: str = "", **fields) -> Path:
        self._path.mkdir(parents=True, exist_ok=True)
        filepath = self._path / filename
        post = frontmatter.Post(content, **fields)
        filepath.write_text(frontmatter.dumps(post))
        log.info("Saved %s", filepath)
        return filepath

    def update(self, filename: str, **fields) -> Path | None:
        filepath = self.find(filename)
        if filepath is None:
            log.error("No file found: %s", filename)
            return None
        post = frontmatter.load(filepath)
        for key, value in fields.items():
            post[key] = value
        filepath.write_text(frontmatter.dumps(post))
        log.info("Updated %s", filepath)
        return filepath

    def archive(self, filename: str, **fields) -> Path | None:
        filepath = self.find(filename)
        if filepath is None:
            log.error("No file found to archive: %s", filename)
            return None
        self._archive.mkdir(parents=True, exist_ok=True)
        post = frontmatter.load(filepath)
        for key, value in fields.items():
            post[key] = value
        dest = self._archive / filename
        dest.write_text(frontmatter.dumps(post))
        filepath.unlink()
        log.info("Archived %s", dest)
        return dest
