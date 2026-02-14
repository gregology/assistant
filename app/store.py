from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from app.config import cfg
from app.mail import Email

log = logging.getLogger(__name__)

NOTES_DIR = cfg("storage.notes_dir", "")
EMAIL_DIR = Path(NOTES_DIR) / "emails"


class EmailStore:
    def __init__(self, path: Path = EMAIL_DIR) -> None:
        self._path = path

    def known_uids(self) -> set[str]:
        uids: set[str] = set()
        if not self._path.is_dir():
            log.info("Email directory does not exist: %s", self._path)
            return uids
        for f in self._path.glob("*.md"):
            try:
                post = frontmatter.load(f)
                uid = post.get("uid")
                if uid is not None:
                    uids.add(str(uid))
            except Exception:
                log.warning("Failed to parse front matter: %s", f)
        return uids

    def find_by_uid(self, uid: str) -> Path | None:
        if not self._path.is_dir():
            return None
        for f in self._path.glob("*.md"):
            try:
                post = frontmatter.load(f)
                if str(post.get("uid")) == uid:
                    return f
            except Exception:
                log.warning("Failed to parse front matter: %s", f)
        return None

    def save(self, email: Email) -> Path:
        self._path.mkdir(parents=True, exist_ok=True)
        filename = email.date.strftime("%Y_%m_%d_%H_%M_%S") + f"__{email._uid}.md"
        filepath = self._path / filename
        post = frontmatter.Post(
            "",
            uid=email._uid,
            from_address=email.from_address,
            to_address=email.to_address,
            subject=email.subject,
            recieved_at=email.date.isoformat(),
            dkim_pass=email.dkim_pass,
            dmarc_pass=email.dmarc_pass,
            spf_pass=email.spf_pass,
        )
        filepath.write_text(frontmatter.dumps(post))
        log.info("Saved email uid=%s to %s", email._uid, filepath)
        return filepath

    def update(self, uid: str, **fields) -> Path | None:
        filepath = self.find_by_uid(uid)
        if filepath is None:
            log.error("No file found for uid=%s", uid)
            return None
        post = frontmatter.load(filepath)
        for key, value in fields.items():
            post[key] = value
        filepath.write_text(frontmatter.dumps(post))
        log.info("Updated email uid=%s at %s", uid, filepath)
        return filepath
