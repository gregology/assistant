from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from app.mail import Email

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


class EmailStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._store = NoteStore(self._path)

    def known_uids(self) -> set[str]:
        uids: set[str] = set()
        for item in self._store.all():
            uid = item.get("uid")
            if uid is not None:
                uids.add(str(uid))
        return uids

    def find_by_uid(self, uid: str) -> Path | None:
        if not self._path.is_dir():
            return None
        matches = list(self._path.glob(f"*__{uid}.md"))
        return matches[0] if matches else None

    def save(self, email: Email) -> Path:
        filename = email.date.strftime("%Y_%m_%d_%H_%M_%S") + f"__{email._uid}.md"
        return self._store.save(
            filename,
            uid=email._uid,
            from_address=email.from_address,
            to_address=email.to_address,
            subject=email.subject,
            recieved_at=email.date.isoformat(),
            authentication=email.authentication,
        )

    def update(self, uid: str, **fields) -> Path | None:
        filepath = self.find_by_uid(uid)
        if filepath is None:
            log.error("No file found for uid=%s", uid)
            return None
        return self._store.update(filepath.name, **fields)
