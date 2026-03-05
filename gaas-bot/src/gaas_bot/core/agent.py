"""Claude agent runner — shared across all gaas-bot commands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)
from pydantic import BaseModel


def summarize_tool_input(input_data: dict[str, Any]) -> str:
    """One-line summary of a tool call's input for console output."""
    if "file_path" in input_data:
        return input_data["file_path"]
    if "pattern" in input_data:
        return input_data["pattern"]
    if "command" in input_data:
        cmd = input_data["command"]
        return cmd[:80] + "..." if len(cmd) > 80 else cmd
    return str(input_data)[:80]


async def run_agent(
    prompt: str,
    *,
    cwd: str | Path,
    allowed_tools: list[str],
    max_turns: int = 30,
    output_model: type[BaseModel] | None = None,
    resume: str | None = None,
    fork_session: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run a Claude agent session and return (structured_output, session_id).

    Streams assistant text and tool use summaries to stdout.
    If output_model is set and no structured output is returned, exits with error.
    """
    options = ClaudeAgentOptions(
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions",
        cwd=str(cwd),
        max_turns=max_turns,
        setting_sources=["project"],
    )

    if output_model is not None:
        options.output_format = {
            "type": "json_schema",
            "schema": output_model.model_json_schema(),
        }

    if resume:
        options.resume = resume
    if fork_session:
        options.fork_session = True

    session_id = None
    result = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
                elif isinstance(block, ToolUseBlock):
                    print(f"  [{block.name}] {summarize_tool_input(block.input)}")
        elif isinstance(message, ResultMessage):
            session_id = getattr(message, "session_id", None)
            if output_model is not None and message.structured_output:
                result = message.structured_output

    if output_model is not None and result is None:
        print("Agent failed: no structured output returned.", file=sys.stderr)
        sys.exit(1)

    return result, session_id
