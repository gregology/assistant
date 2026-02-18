from __future__ import annotations

import logging
from pathlib import Path

from app.store import NoteStore

from .mail import Email

log = logging.getLogger(__name__)


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
