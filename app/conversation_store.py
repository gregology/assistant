"""File-backed conversation storage using JSONL.

Each conversation is a single .jsonl file where every line is a
self-contained JSON object representing one message.  Append-only
writes keep the file safe from partial-write corruption.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from assistant_sdk.logging import get_logger

log = get_logger(__name__)


class ConversationStore:
    """JSONL-backed conversation storage.

    One file per conversation at ``{directory}/{conversation_id}.jsonl``.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def create(self) -> str:
        """Create a new empty conversation file and return its ID."""
        conversation_id = secrets.token_hex(8)
        path = self._dir / f"{conversation_id}.jsonl"
        path.touch()
        return conversation_id

    def exists(self, conversation_id: str) -> bool:
        """Check whether a conversation file exists."""
        return (self._dir / f"{conversation_id}.jsonl").is_file()

    def append(
        self,
        conversation_id: str,
        role: str,
        msg_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a message to a conversation's JSONL file."""
        path = self._dir / f"{conversation_id}.jsonl"
        if not path.is_file():
            raise KeyError(conversation_id)
        record: dict[str, Any] = {
            "role": role,
            "type": msg_type,
            "content": content,
            "ts": datetime.now(UTC).isoformat(),
        }
        if metadata is not None:
            record["metadata"] = metadata
        line = json.dumps(record, separators=(",", ":")) + "\n"
        # O_APPEND is used for crash safety (no partial overwrites), not
        # multi-writer atomicity. Single-writer architecture assumed.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)

    def read(self, conversation_id: str) -> list[dict[str, Any]]:
        """Read all messages from a conversation.

        Raises ``KeyError`` if the conversation does not exist.
        """
        path = self._dir / f"{conversation_id}.jsonl"
        if not path.is_file():
            raise KeyError(conversation_id)
        messages: list[dict[str, Any]] = []
        text = path.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line:
                messages.append(json.loads(line))
        return messages

    def clear(self, conversation_id: str) -> None:
        """Truncate a conversation file, removing all messages."""
        path = self._dir / f"{conversation_id}.jsonl"
        if not path.is_file():
            raise KeyError(conversation_id)
        path.write_text("")

    def list_conversations(self) -> list[dict[str, Any]]:
        """List conversations with metadata, sorted by last activity descending."""
        conversations: list[dict[str, Any]] = []
        for path in self._dir.glob("*.jsonl"):
            cid = path.stem
            lines = path.read_text().splitlines()
            non_empty = [ln for ln in lines if ln.strip()]
            if not non_empty:
                conversations.append(
                    {
                        "id": cid,
                        "message_count": 0,
                        "created_at": datetime.fromtimestamp(
                            path.stat().st_ctime,
                            tz=UTC,
                        ).isoformat(),
                        "last_activity": datetime.fromtimestamp(
                            path.stat().st_mtime,
                            tz=UTC,
                        ).isoformat(),
                    }
                )
                continue
            first = json.loads(non_empty[0])
            last = json.loads(non_empty[-1])
            conversations.append(
                {
                    "id": cid,
                    "message_count": len(non_empty),
                    "created_at": first.get("ts", ""),
                    "last_activity": last.get("ts", ""),
                }
            )
        conversations.sort(key=lambda c: c["last_activity"], reverse=True)
        return conversations

    def find_proposal(
        self,
        conversation_id: str,
        proposal_id: str,
    ) -> dict[str, Any] | None:
        """Find a confirmation message by proposal_id, scanning backward."""
        messages = self.read(conversation_id)
        for msg in reversed(messages):
            meta = msg.get("metadata")
            if meta and meta.get("proposal_id") == proposal_id:
                return msg
        return None

    def has_response(
        self,
        conversation_id: str,
        proposal_id: str,
    ) -> bool:
        """Check whether a response message already exists for a proposal."""
        messages = self.read(conversation_id)
        for msg in reversed(messages):
            if msg.get("type") != "response":
                continue
            meta = msg.get("metadata")
            if meta and meta.get("proposal_id") == proposal_id:
                return True
        return False
