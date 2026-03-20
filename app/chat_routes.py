"""Chat API endpoints.

JSON API under /api/chat for creating conversations, sending messages,
and polling for task completion.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, UTC
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import queue
from app.chat import chat_service, ChatMessage
from assistant_sdk.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/chat")


class MessageRequest(BaseModel):
    content: str


@router.post("/conversations")
async def create_conversation() -> dict[str, str]:
    conversation_id = chat_service.create_conversation()
    return {"conversation_id": conversation_id}


@router.get("/conversations/{conversation_id}/history")
async def get_history(conversation_id: str) -> dict[str, Any]:
    try:
        messages = chat_service.get_history(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found") from None
    return {
        "conversation_id": conversation_id,
        "messages": [asdict(m) for m in messages],
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, body: MessageRequest) -> dict[str, Any]:
    try:
        result = chat_service.handle_message(conversation_id, body.content)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found") from None

    if result["type"] == "command":
        return {"type": "command", "message": asdict(result["message"])}
    return {"type": "chat", "task_id": result["task_id"]}


@router.get("/tasks/{task_id}")
async def poll_task(task_id: str) -> dict[str, Any]:
    filename = f"{task_id}.yaml"

    # Check done/
    done_path = queue.BASE_DIR / "done" / filename
    if done_path.exists():
        task = yaml.safe_load(done_path.read_text())
        result = task.get("result", {})
        content = result.get("content", "")
        conversation_id = task.get("payload", {}).get("conversation_id", "")
        msg = chat_service.receive_reply(conversation_id, content)
        return {"status": "done", "message": asdict(msg)}

    # Check failed/
    failed_path = queue.BASE_DIR / "failed" / filename
    if failed_path.exists():
        task = yaml.safe_load(failed_path.read_text())
        error = task.get("error", "Unknown error")
        conversation_id = task.get("payload", {}).get("conversation_id", "")
        msg = ChatMessage(
            role="system",
            content=f"LLM request failed: {error}",
            type="system",
            timestamp=datetime.now(UTC).isoformat(),
        )
        if conversation_id and conversation_id in chat_service._conversations:
            chat_service._conversations[conversation_id].append(msg)
        return {"status": "failed", "message": asdict(msg)}

    # Check pending/ or active/
    pending_path = queue.BASE_DIR / "pending" / filename
    active_path = queue.BASE_DIR / "active" / filename
    if pending_path.exists() or active_path.exists():
        return {"status": "pending"}

    raise HTTPException(status_code=404, detail="Task not found")
