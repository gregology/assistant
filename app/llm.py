from __future__ import annotations

import json
import logging
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import httpx
import jsonschema
from pydantic import BaseModel, Field

from app.config import cfg

log = logging.getLogger(__name__)

MODEL_ALIASES: dict[str, str] = {
    "fast": cfg("llm.fast_model", ""),
}



class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    role: Role
    content: str


class MessageList:
    def __init__(self) -> None:
        self._messages: list[Message] = []

    def append(self, message: Message) -> None:
        self._messages.append(message)

    def all(self) -> list[Message]:
        return list(self._messages)

    def first(self) -> Message | None:
        return self._messages[0] if self._messages else None

    def last(self) -> Message | None:
        return self._messages[-1] if self._messages else None

    def last_user(self) -> Message | None:
        return self._last_by_role(Role.USER)

    def last_agent(self) -> Message | None:
        return self._last_by_role(Role.ASSISTANT)

    def to_api_format(self) -> list[dict[str, str]]:
        return [m.model_dump() for m in self._messages]

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self):
        return iter(self._messages)

    def _last_by_role(self, role: Role) -> Message | None:
        for msg in reversed(self._messages):
            if msg.role == role:
                return msg
        return None


class RetryConfig(BaseModel):
    max_attempts: int = 3
    temp_adjustments: list[float] = Field(
        default_factory=lambda: [0.0, 0.2, 0.5]
    )

    def get_temp_adjustment(self, attempt: int) -> float:
        if not self.temp_adjustments:
            return 0.0
        idx = min(attempt, len(self.temp_adjustments) - 1)
        return self.temp_adjustments[idx]


@runtime_checkable
class LLMBackend(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        response_format: dict | None = None,
    ) -> str: ...


class LlamaCppBackend:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 2400.0,
    ):
        self._base_url = (base_url or cfg("llm.base_url", "http://localhost:8000")).rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        response_format: dict | None = None,
    ) -> str:
        resolved = MODEL_ALIASES.get(model, model)

        payload: dict[str, Any] = {
            "model": resolved,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        log.info(
            "LLM request model=%s messages=%d temperature=%.2f schema=%s",
            resolved,
            len(messages),
            temperature,
            response_format is not None,
        )

        resp = self._client.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
        )
        if not resp.is_success:
            log.error("LLM error status=%d body=%s", resp.status_code, resp.text)
        resp.raise_for_status()

        content = resp.json()["choices"][0]["message"]["content"]
        log.info("LLM response length=%d", len(content))
        return content


class SchemaValidationError(Exception):
    def __init__(self, message: str, raw_content: str, errors: list[str]):
        super().__init__(message)
        self.raw_content = raw_content
        self.errors = errors


class LLMConversation:
    def __init__(
        self,
        model: str = "fast",
        temperature: float = 0.7,
        system: str | None = None,
        backend: LLMBackend | None = None,
        retry: RetryConfig | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.messages = MessageList()
        self._backend = backend or LlamaCppBackend()
        self._retry = retry or RetryConfig()

        if system is not None:
            self.messages.append(Message(role=Role.SYSTEM, content=system))

    def message(self, prompt: str, schema: dict | None = None) -> str | dict:
        self.messages.append(Message(role=Role.USER, content=prompt))

        if schema is None:
            return self._send_plain()
        return self._send_structured(schema)

    def _send_plain(self) -> str:
        content = self._backend.chat(
            messages=self.messages.to_api_format(),
            model=self.model,
            temperature=self.temperature,
        )
        self.messages.append(Message(role=Role.ASSISTANT, content=content))
        return content

    def _send_structured(self, schema: dict) -> dict:
        response_format = _wrap_schema(schema)
        raw_content = ""
        errors: list[str] = []

        for attempt in range(self._retry.max_attempts):
            temp_adj = self._retry.get_temp_adjustment(attempt)
            effective_temp = self.temperature + temp_adj

            raw_content = self._backend.chat(
                messages=self.messages.to_api_format(),
                model=self.model,
                temperature=effective_temp,
                response_format=response_format,
            )

            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                log.warning(
                    "LLM returned invalid JSON attempt %d/%d: %s",
                    attempt + 1,
                    self._retry.max_attempts,
                    exc,
                )
                continue

            errors = _validate_schema(parsed, schema)
            if not errors:
                self.messages.append(
                    Message(role=Role.ASSISTANT, content=raw_content)
                )
                return parsed

            log.warning(
                "Schema validation failed attempt %d/%d: %s",
                attempt + 1,
                self._retry.max_attempts,
                errors,
            )

        # All retries exhausted — remove the dangling user message
        self.messages._messages.pop()
        raise SchemaValidationError(
            f"Failed to get valid structured output after "
            f"{self._retry.max_attempts} attempts",
            raw_content=raw_content,
            errors=errors,
        )


def _wrap_schema(schema: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_output",
            "schema": {
                "type": "object",
                **schema,
            },
        },
    }


def _validate_schema(data: Any, schema: dict) -> list[str]:
    full_schema = {"type": "object", **schema}
    validator = jsonschema.Draft202012Validator(full_schema)
    return [e.message for e in validator.iter_errors(data)]
