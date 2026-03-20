"""Chat service layer.

Manages conversation state (in-memory) and coordinates with the task queue
for LLM processing.  The ChatService lives in the API process; the
chat_message_handler runs in the worker process.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any

from app import queue
from app.config import config, LLMConfig
from app.llm import ChatCompletionsBackend
from assistant_sdk.logging import get_logger
from assistant_sdk.task import TaskRecord

log = get_logger(__name__)

AVAILABLE_COMMANDS = ["/clear"]


@dataclass
class ChatMessage:
    """A single message in a chat conversation."""
    role: str          # "user", "assistant", "system"
    content: str
    type: str          # "chat" for LLM-generated, "command" for system/programmatic
    timestamp: str     # ISO 8601


class ChatService:
    def __init__(self) -> None:
        self._conversations: dict[str, list[ChatMessage]] = {}

    def create_conversation(self) -> str:
        conversation_id = str(uuid.uuid4())
        self._conversations[conversation_id] = []
        return conversation_id

    def get_history(self, conversation_id: str) -> list[ChatMessage]:
        if conversation_id not in self._conversations:
            raise KeyError(conversation_id)
        return list(self._conversations[conversation_id])

    def handle_message(self, conversation_id: str, content: str) -> dict[str, Any]:
        if conversation_id not in self._conversations:
            raise KeyError(conversation_id)

        if content.startswith("/"):
            return self._handle_command(conversation_id, content)

        # Store user message
        user_msg = ChatMessage(
            role="user",
            content=content,
            type="chat",
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._conversations[conversation_id].append(user_msg)

        # Build LLM message list and enqueue
        messages = self._build_llm_messages(conversation_id)
        payload: dict[str, Any] = {
            "type": "chat.message",
            "conversation_id": conversation_id,
            "llm_profile": config.chat.llm,
            "messages": messages,
            "on_result": [
                {"type": "chat_reply", "conversation_id": conversation_id},
            ],
        }
        task_id = queue.enqueue(payload, priority=1)
        return {"type": "chat", "task_id": task_id}

    def receive_reply(self, conversation_id: str, content: str) -> ChatMessage:
        msg = ChatMessage(
            role="assistant",
            content=content,
            type="chat",
            timestamp=datetime.now(UTC).isoformat(),
        )
        if conversation_id in self._conversations:
            self._conversations[conversation_id].append(msg)
        return msg

    def clear_conversation(self, conversation_id: str) -> ChatMessage:
        if conversation_id not in self._conversations:
            raise KeyError(conversation_id)
        self._conversations[conversation_id] = []
        msg = ChatMessage(
            role="system",
            content="Conversation cleared.",
            type="command",
            timestamp=datetime.now(UTC).isoformat(),
        )
        return msg

    def _build_llm_messages(self, conversation_id: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if config.chat.system_prompt:
            messages.append({"role": "system", "content": config.chat.system_prompt})
        for msg in self._conversations.get(conversation_id, []):
            if msg.type == "chat":
                messages.append({"role": msg.role, "content": msg.content})
        return messages

    def _handle_command(self, conversation_id: str, content: str) -> dict[str, Any]:
        command = content.strip().split()[0]

        # Store the user command message
        user_msg = ChatMessage(
            role="user",
            content=content,
            type="command",
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._conversations[conversation_id].append(user_msg)

        if command == "/clear":
            msg = self.clear_conversation(conversation_id)
            return {"type": "command", "message": msg}

        # Unknown command
        msg = ChatMessage(
            role="system",
            content=f"Unknown command: {command}. "
            f"Available commands: {', '.join(AVAILABLE_COMMANDS)}",
            type="command",
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._conversations[conversation_id].append(msg)
        return {"type": "command", "message": msg}


def chat_message_handler(task: TaskRecord) -> dict[str, Any]:
    """Worker handler for chat.message tasks."""
    payload = task["payload"]
    llm_profile = payload["llm_profile"]
    messages = payload["messages"]
    conversation_id = payload["conversation_id"]

    llm_config: LLMConfig = config.llms[llm_profile]
    backend = ChatCompletionsBackend(
        base_url=llm_config.base_url,
        token=llm_config.token,
    )
    response = backend.chat(
        messages=messages,
        model=llm_config.model,
        parameters=llm_config.parameters,
    )
    return {"content": response.content, "conversation_id": conversation_id}


# Module-level singleton
chat_service = ChatService()
