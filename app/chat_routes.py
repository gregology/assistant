"""Chat API endpoints.

JSON API under /api/chat for creating conversations, sending messages,
polling for task completion, and responding to proposals.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import queue
from app.chat import chat_service
from assistant_sdk.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/chat")


def _extract_conversation_id(
    result: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    """Extract conversation_id from either the result or on_result config."""
    cid: str = result.get("conversation_id", "")
    if cid:
        return cid
    for route in payload.get("on_result", []):
        if route.get("type") == "chat_reply":
            return str(route.get("conversation_id", ""))
    return str(payload.get("conversation_id", ""))


class MessageRequest(BaseModel):
    content: str


class ProposalResponse(BaseModel):
    option: str


@router.get("/conversations")
async def list_conversations() -> dict[str, Any]:
    return {"conversations": chat_service.list_conversations()}


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


@router.post("/conversations/{conversation_id}/proposals/{proposal_id}")
async def respond_to_proposal(
    conversation_id: str,
    proposal_id: str,
    body: ProposalResponse,
) -> dict[str, Any]:
    try:
        result = chat_service.handle_proposal_response(
            conversation_id,
            proposal_id,
            body.option,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    response: dict[str, Any] = {"message": asdict(result["message"])}
    if result["type"] == "task":
        response["task_id"] = result["task_id"]
    return response


@router.get("/tasks/{task_id}")
async def poll_task(task_id: str) -> dict[str, Any]:
    filename = f"{task_id}.yaml"

    # Check done/
    done_path = queue.BASE_DIR / "done" / filename
    if done_path.exists():
        task = yaml.safe_load(done_path.read_text())
        result = task.get("result", {})
        payload = task.get("payload", {})
        conversation_id = _extract_conversation_id(result, payload)

        cached = chat_service.check_task_processed(task_id, conversation_id)
        if cached is not None:
            return {"status": "done", "messages": [asdict(m) for m in cached]}

        if "structured" in result:
            messages = chat_service.receive_structured_reply(
                conversation_id,
                result["structured"],
                task_id=task_id,
            )
        elif "content" in result:
            messages = chat_service.receive_reply(
                conversation_id,
                result["content"],
                task_id=task_id,
            )
        else:
            # Service task result — use "text" field
            text = result.get("text", "Action completed.")
            if not result.get("text"):
                log.warning("Task %s result has no text/content/structured key", task_id)
            messages = chat_service.receive_service_result(
                conversation_id,
                text,
                task_id=task_id,
            )

        chat_service.mark_task_processed(task_id, messages)
        return {"status": "done", "messages": [asdict(m) for m in messages]}

    # Check failed/
    failed_path = queue.BASE_DIR / "failed" / filename
    if failed_path.exists():
        task = yaml.safe_load(failed_path.read_text())
        payload = task.get("payload", {})
        conversation_id = _extract_conversation_id({}, payload)

        cached = chat_service.check_task_processed(task_id, conversation_id)
        if cached is not None:
            return {"status": "failed", "messages": [asdict(m) for m in cached]}

        error = task.get("error", "Unknown error")
        task_type = payload.get("type", "Task")
        msg = chat_service.record_error(
            conversation_id,
            f"{task_type} failed: {error}",
        )
        chat_service.mark_task_processed(task_id, [msg])
        return {"status": "failed", "messages": [asdict(msg)]}

    # Check pending/ or active/
    pending_path = queue.BASE_DIR / "pending" / filename
    active_path = queue.BASE_DIR / "active" / filename
    if pending_path.exists() or active_path.exists():
        return {"status": "pending"}

    raise HTTPException(status_code=404, detail="Task not found")
