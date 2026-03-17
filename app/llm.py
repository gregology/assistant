from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import httpx
import jsonschema
from pydantic import BaseModel

from app.config import config

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    duration_s: float

    @property
    def tokens_per_sec(self) -> float:
        return self.completion_tokens / self.duration_s if self.duration_s > 0 else 0.0


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

    def pop(self) -> Message:
        return self._messages.pop()

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

    def __iter__(self) -> Any:
        return iter(self._messages)

    def _last_by_role(self, role: Role) -> Message | None:
        for msg in reversed(self._messages):
            if msg.role == role:
                return msg
        return None


@runtime_checkable
class LLMBackend(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        parameters: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


class ChatCompletionsBackend:
    """Backend using the /v1/chat/completions endpoint format."""
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 2400.0,
    ):
        self._base_url = (base_url or "http://localhost:11434").rstrip("/")
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(timeout=timeout, headers=headers)

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        parameters: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **(parameters or {}),
        }
        if response_format is not None:
            payload["response_format"] = response_format

        log.info(
            "LLM request model=%s messages=%d params=%s schema=%s",
            model,
            len(messages),
            parameters or {},
            response_format is not None,
        )

        t0 = time.perf_counter()
        resp = self._client.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
        )
        duration = time.perf_counter() - t0

        if not resp.is_success:
            log.error(
                "LLM error status=%d duration=%.2fs body=%s",
                resp.status_code,
                duration,
                resp.text,
            )
        resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        return LLMResponse(
            content=content,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_s=duration,
        )


class SchemaValidationError(Exception):
    def __init__(self, message: str, raw_content: str, errors: list[str]):
        super().__init__(message)
        self.raw_content = raw_content
        self.errors = errors


class LLMConversation:
    MAX_RETRIES = 3

    def __init__(
        self,
        model: str = "default",
        system: str | None = None,
        backend: LLMBackend | None = None,
    ):
        llm_config = config.llms.get(model)
        if llm_config is None:
            raise ValueError(
                f"Unknown LLM profile '{model}'. "
                f"Available: {list(config.llms.keys())}"
            )
        self.model = llm_config.model
        self._parameters = llm_config.parameters
        self.messages = MessageList()
        self._backend = backend or ChatCompletionsBackend(
            base_url=llm_config.base_url,
            token=llm_config.token,
        )

        if system is not None:
            self.messages.append(Message(role=Role.SYSTEM, content=system))

    def message(self, prompt: str, schema: dict[str, Any] | None = None) -> str | dict[str, Any]:
        self.messages.append(Message(role=Role.USER, content=prompt))

        if schema is None:
            return self._send_plain()
        return self._send_structured(schema)

    def _log_stats(self, response: LLMResponse, attempt: int | None = None) -> None:
        attempt_str = f" attempt={attempt}/{self.MAX_RETRIES}" if attempt is not None else ""
        log.info(
            "LLM stats model=%s duration=%.2fs prompt_tokens=%d"
            " completion_tokens=%d tokens_per_sec=%.1f%s",
            response.model,
            response.duration_s,
            response.prompt_tokens,
            response.completion_tokens,
            response.tokens_per_sec,
            attempt_str,
        )

    def _send_plain(self) -> str:
        response = self._backend.chat(
            messages=self.messages.to_api_format(),
            model=self.model,
            parameters=self._parameters,
        )
        self._log_stats(response)
        self.messages.append(Message(role=Role.ASSISTANT, content=response.content))
        return response.content

    def _send_structured(self, schema: dict[str, Any]) -> dict[str, Any]:
        response_format = _wrap_schema(schema)
        raw_content = ""
        errors: list[str] = []

        for attempt in range(self.MAX_RETRIES):
            response = self._backend.chat(
                messages=self.messages.to_api_format(),
                model=self.model,
                parameters=self._parameters,
                response_format=response_format,
            )
            self._log_stats(response, attempt=attempt + 1)
            raw_content = response.content

            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                log.warning(
                    "LLM returned invalid JSON attempt %d/%d: %s",
                    attempt + 1,
                    self.MAX_RETRIES,
                    exc,
                )
                continue

            errors = _validate_schema(parsed, schema)
            if not errors:
                self.messages.append(
                    Message(role=Role.ASSISTANT, content=raw_content)
                )
                return parsed  # type: ignore[no-any-return]

            log.warning(
                "Schema validation failed attempt %d/%d: %s",
                attempt + 1,
                self.MAX_RETRIES,
                errors,
            )

        # All retries exhausted — remove the dangling user message
        self.messages.pop()
        raise SchemaValidationError(
            f"Failed to get valid structured output after "
            f"{self.MAX_RETRIES} attempts",
            raw_content=raw_content,
            errors=errors,
        )


def _wrap_schema(schema: dict[str, Any]) -> dict[str, Any]:
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


def _validate_schema(data: Any, schema: dict[str, Any]) -> list[str]:
    full_schema = {"type": "object", **schema}
    validator = jsonschema.Draft202012Validator(full_schema)
    return [e.message for e in validator.iter_errors(data)]
