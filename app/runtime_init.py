"""Register app-level implementations with the assistant_sdk runtime.

Must be called once at startup, before any integration code that uses
assistant_sdk.runtime functions.
"""

from __future__ import annotations

import assistant_sdk.runtime
from app.config import config
from app.llm import ChatCompletionsBackend, LLMConversation
from app.queue_policy import policy_enqueue


def register_runtime() -> None:
    """Wire up app implementations to the SDK runtime slots."""
    backends = {
        name: ChatCompletionsBackend(base_url=llm.base_url, token=llm.token)
        for name, llm in config.llms.items()
    }
    assistant_sdk.runtime.register(
        enqueue=policy_enqueue,
        get_integration=config.get_integration,
        get_platform=config.get_platform,
        create_llm_conversation=lambda model="default", system=None: LLMConversation(
            model, system, backend=backends.get(model)
        ),
        get_llm_config=lambda profile="default": config.llms[profile],  # type: ignore[misc]
        get_notes_dir=lambda: config.directories.notes,
    )
