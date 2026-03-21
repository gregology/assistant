"""Chat service layer.

Manages conversation state (file-backed JSONL) and coordinates with the
task queue for LLM processing.  The ChatService lives in the API process;
the chat_message_handler runs in the worker process.
"""

from __future__ import annotations

import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from app import queue
from app.config import config, LLMConfig
from app.conversation_store import ConversationStore
from app.llm import ChatCompletionsBackend, _wrap_schema
from assistant_sdk.logging import get_logger
from assistant_sdk.task import TaskRecord

log = get_logger(__name__)

AVAILABLE_COMMANDS = ["/clear"]


@dataclass
class ChatMessage:
    """A single message in a chat conversation."""

    role: str  # "user", "assistant", "system"
    content: str
    type: str  # "chat", "command", "confirmation", "response"
    timestamp: str  # ISO 8601
    metadata: dict[str, Any] | None = field(default=None)


_TASK_CACHE_MAX = 256


class ChatService:
    def __init__(self, store: ConversationStore | None = None) -> None:
        if store is None:
            store = ConversationStore(config.directories.chats)
        self._store = store
        self._processed_tasks: OrderedDict[str, list[ChatMessage]] = OrderedDict()

    def create_conversation(self) -> str:
        return self._store.create()

    def get_history(self, conversation_id: str) -> list[ChatMessage]:
        rows = self._store.read(conversation_id)  # raises KeyError
        return [
            ChatMessage(
                role=r["role"],
                content=r["content"],
                type=r["type"],
                timestamp=r["ts"],
                metadata=r.get("metadata"),
            )
            for r in rows
        ]

    def list_conversations(self) -> list[dict[str, Any]]:
        return self._store.list_conversations()

    def handle_message(self, conversation_id: str, content: str) -> dict[str, Any]:
        if not self._store.exists(conversation_id):
            raise KeyError(conversation_id)

        if content.startswith("/"):
            return self._handle_command(conversation_id, content)

        # Store user message
        self._store.append(conversation_id, "user", "chat", content)

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

    def check_task_processed(
        self,
        task_id: str,
        conversation_id: str = "",
    ) -> list[ChatMessage] | None:
        """Return cached messages if this task was already processed.

        Checks the in-memory cache first. On a cache miss (e.g., after
        restart), scans the JSONL for a message tagged with this task_id.
        """
        cached = self._processed_tasks.get(task_id)
        if cached is not None:
            return cached
        # Survive restarts: check the JSONL for a message with this task_id
        if conversation_id and self._store.exists(conversation_id):
            for row in self._store.read(conversation_id):
                meta = row.get("metadata")
                if meta and meta.get("task_id") == task_id:
                    return []  # already processed, return empty to signal "skip"
        return None

    def mark_task_processed(self, task_id: str, messages: list[ChatMessage]) -> None:
        """Cache messages for a processed task to prevent duplicate appends."""
        self._processed_tasks[task_id] = messages
        while len(self._processed_tasks) > _TASK_CACHE_MAX:
            self._processed_tasks.popitem(last=False)

    def receive_reply(
        self,
        conversation_id: str,
        content: str,
        task_id: str = "",
    ) -> list[ChatMessage]:
        """Record a plain-text assistant reply. Returns a list of messages."""
        now = datetime.now(UTC).isoformat()
        metadata = {"task_id": task_id} if task_id else None
        msg = ChatMessage(
            role="assistant",
            content=content,
            type="chat",
            timestamp=now,
            metadata=metadata,
        )
        if self._store.exists(conversation_id):
            self._store.append(
                conversation_id,
                msg.role,
                msg.type,
                msg.content,
                metadata=metadata,
            )
        return [msg]

    def receive_service_result(
        self,
        conversation_id: str,
        text: str,
        task_id: str = "",
    ) -> list[ChatMessage]:
        """Record the result of a service task. Returns a list of messages."""
        now = datetime.now(UTC).isoformat()
        metadata = {"task_id": task_id} if task_id else None
        msg = ChatMessage(
            role="system",
            content=text,
            type="system",
            timestamp=now,
            metadata=metadata,
        )
        if self._store.exists(conversation_id):
            self._store.append(
                conversation_id,
                msg.role,
                msg.type,
                msg.content,
                metadata=metadata,
            )
        return [msg]

    def receive_structured_reply(
        self,
        conversation_id: str,
        structured: dict[str, Any],
        task_id: str = "",
    ) -> list[ChatMessage]:
        """Process a structured LLM response with an optional proposal.

        Returns one message (reply only) or two (reply + confirmation).
        """
        messages: list[ChatMessage] = []
        now = datetime.now(UTC).isoformat()

        # Always store the assistant's reply
        reply_metadata = {"task_id": task_id} if task_id else None
        reply_msg = ChatMessage(
            role="assistant",
            content=structured["reply"],
            type="chat",
            timestamp=now,
            metadata=reply_metadata,
        )
        messages.append(reply_msg)
        if self._store.exists(conversation_id):
            self._store.append(
                conversation_id,
                reply_msg.role,
                reply_msg.type,
                reply_msg.content,
                metadata=reply_metadata,
            )

        # If a proposal is present, store a confirmation message
        proposal = structured.get("proposal")
        if proposal:
            proposal_id = secrets.token_hex(4)
            action = proposal["action"]
            options = ACTION_OPTIONS.get(action, DEFAULT_OPTIONS)
            metadata = {
                "proposal_id": proposal_id,
                "action": action,
                "parameters": proposal.get("parameters", {}),
                "description": proposal.get("description", ""),
                "options": [{"id": o["id"], "label": o["label"]} for o in options],
                "status": "pending",
            }
            confirmation_msg = ChatMessage(
                role="system",
                content=proposal.get("description", f"Proposed action: {action}"),
                type="confirmation",
                timestamp=now,
                metadata=metadata,
            )
            messages.append(confirmation_msg)
            if self._store.exists(conversation_id):
                self._store.append(
                    conversation_id,
                    confirmation_msg.role,
                    confirmation_msg.type,
                    confirmation_msg.content,
                    metadata=confirmation_msg.metadata,
                )

        return messages

    def handle_proposal_response(
        self,
        conversation_id: str,
        proposal_id: str,
        option: str,
    ) -> dict[str, Any]:
        """Handle a user's response to a proposal confirmation.

        Returns ``{"type": "immediate", "message": ChatMessage}`` for
        rejections/errors, or ``{"type": "task", "message": ChatMessage,
        "task_id": str}`` when an action is enqueued for async execution.
        """
        if not self._store.exists(conversation_id):
            raise KeyError(conversation_id)

        proposal_msg = self._store.find_proposal(conversation_id, proposal_id)
        if proposal_msg is None:
            raise ValueError(f"Proposal {proposal_id} not found")

        # Prevent double-approval after page reload
        if self._store.has_response(conversation_id, proposal_id):
            raise ValueError(f"Proposal {proposal_id} already responded to")

        metadata = proposal_msg.get("metadata", {})

        # Validate the option is in the allowed set
        valid_options = {o["id"] for o in metadata.get("options", [])}
        if option not in valid_options:
            raise ValueError(f"Invalid option '{option}'. Valid: {valid_options}")

        # Record the user's response
        self._store.append(
            conversation_id,
            "user",
            "response",
            option,
            metadata={"proposal_id": proposal_id, "option": option},
        )

        # Rejections are immediate
        if option == "reject":
            msg = ChatMessage(
                role="system",
                content="Action cancelled.",
                type="system",
                timestamp=datetime.now(UTC).isoformat(),
            )
            self._store.append(
                conversation_id,
                msg.role,
                msg.type,
                msg.content,
            )
            return {"type": "immediate", "message": msg}

        action = metadata.get("action", "")
        params = metadata.get("parameters", {})

        service_type = ACTION_REGISTRY.get(action)
        if service_type is None:
            msg = ChatMessage(
                role="system",
                content=f"Unknown action: {action}",
                type="system",
                timestamp=datetime.now(UTC).isoformat(),
            )
            self._store.append(
                conversation_id,
                msg.role,
                msg.type,
                msg.content,
            )
            return {"type": "immediate", "message": msg}

        # Enqueue the service task through the normal queue
        payload: dict[str, Any] = {
            "type": service_type,
            "inputs": params,
            "on_result": [
                {"type": "chat_reply", "conversation_id": conversation_id},
            ],
        }
        task_id = queue.enqueue(payload, priority=2)
        log.human(
            "Chat action approved: %s (task %s)",
            action,
            task_id,
        )

        msg = ChatMessage(
            role="system",
            content="Processing...",
            type="system",
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._store.append(
            conversation_id,
            msg.role,
            msg.type,
            msg.content,
        )
        return {"type": "task", "message": msg, "task_id": task_id}

    def record_error(self, conversation_id: str, content: str) -> ChatMessage:
        """Record a system error message in the conversation."""
        msg = ChatMessage(
            role="system",
            content=content,
            type="system",
            timestamp=datetime.now(UTC).isoformat(),
        )
        if self._store.exists(conversation_id):
            self._store.append(conversation_id, msg.role, msg.type, msg.content)
        return msg

    def clear_conversation(self, conversation_id: str) -> ChatMessage:
        self._store.clear(conversation_id)  # raises KeyError if missing
        return ChatMessage(
            role="system",
            content="Conversation cleared.",
            type="command",
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _build_llm_messages(self, conversation_id: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        system_prompt = _build_system_prompt()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for row in self._store.read(conversation_id):
            if row["type"] == "chat":
                messages.append({"role": row["role"], "content": row["content"]})
        return messages

    def _handle_command(self, conversation_id: str, content: str) -> dict[str, Any]:
        command = content.strip().split()[0]

        # Store the user command message
        self._store.append(conversation_id, "user", "command", content)

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
        self._store.append(conversation_id, msg.role, msg.type, msg.content)
        return {"type": "command", "message": msg}


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(  # nosec B701 — plaintext templates, not HTML
    loader=FileSystemLoader(_TEMPLATES_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def _build_system_prompt() -> str:
    """Build the full system prompt from user config + registered actions."""
    parts: list[str] = []

    if config.chat.system_prompt:
        parts.append(config.chat.system_prompt)

    action_block = _build_action_prompt()
    if action_block:
        parts.append(action_block)

    return "\n\n".join(parts)


def _build_action_prompt() -> str:
    """Build an instruction block describing available actions.

    Rendered from the action_prompt.jinja template, which is populated at
    startup from ACTION_METADATA (registered by integrations).  Returns
    empty string if no actions are registered.
    """
    if not ACTION_METADATA:
        return ""
    template = _jinja_env.get_template("action_prompt.jinja")
    return template.render(actions=ACTION_METADATA).strip()


# ---------------------------------------------------------------------------
# Action registry — populated at startup by integration registration
# ---------------------------------------------------------------------------

# Maps action name -> service task type (e.g., "service.github.create_issue").
# Populated by _register_single_service when a service has a chat config.
ACTION_REGISTRY: dict[str, str] = {}

# Maps action name -> list of response options for the confirmation UI.
# The system attaches these to proposals; the LLM never chooses them.
DEFAULT_OPTIONS = [
    {"id": "approve", "label": "Approve"},
    {"id": "reject", "label": "Cancel"},
]
ACTION_OPTIONS: dict[str, list[dict[str, str]]] = {}

# Maps action name -> {description, input_schema} for building system prompts.
# Populated alongside ACTION_REGISTRY during registration.
ACTION_METADATA: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Structured output schema for chat responses
# ---------------------------------------------------------------------------

CHAT_RESPONSE_SCHEMA: dict[str, Any] = {
    "properties": {
        "reply": {
            "type": "string",
            "description": "The assistant's conversational reply to the user.",
        },
        "proposal": {
            "type": "object",
            "description": (
                "If the user is requesting an action that requires confirmation, "
                "propose it here. Omit this field for normal conversation."
            ),
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The action type identifier.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Action-specific parameters.",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Human-readable summary of what will happen if the user approves."
                    ),
                },
            },
            "required": ["action", "parameters", "description"],
        },
    },
    "required": ["reply"],
}


# ---------------------------------------------------------------------------
# Worker handler (runs in worker process)
# ---------------------------------------------------------------------------


def chat_message_handler(task: TaskRecord) -> dict[str, Any]:
    """Worker handler for chat.message tasks.

    Requests structured JSON output from the LLM.  If the LLM returns
    valid JSON matching CHAT_RESPONSE_SCHEMA, the result is returned as
    ``{"structured": {...}, "conversation_id": ...}``.  If parsing fails
    the raw text is returned as ``{"content": ..., "conversation_id": ...}``
    so the reply still reaches the user.
    """
    payload = task["payload"]
    llm_profile = payload["llm_profile"]
    messages = payload["messages"]
    conversation_id = payload["conversation_id"]

    llm_config: LLMConfig = config.llms[llm_profile]
    backend = ChatCompletionsBackend(
        base_url=llm_config.base_url,
        token=llm_config.token,
    )

    # Only request structured output when actions are registered.
    # Some LLM backends (notably Ollama) hang on oneOf schemas.
    use_structured = bool(ACTION_METADATA)
    response_format = _wrap_schema(CHAT_RESPONSE_SCHEMA) if use_structured else None

    response = backend.chat(
        messages=messages,
        model=llm_config.model,
        parameters=llm_config.parameters,
        response_format=response_format,
    )

    # Try to parse structured output; fall back to plain text
    if use_structured:
        try:
            parsed = json.loads(response.content)
            if "reply" in parsed:
                return {"structured": parsed, "conversation_id": conversation_id}
        except (json.JSONDecodeError, TypeError):
            log.warning("Chat response was not valid JSON, falling back to plain text")

    return {"content": response.content, "conversation_id": conversation_id}


# Module-level singleton
chat_service = ChatService()
