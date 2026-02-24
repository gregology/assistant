from app.integrations.email.platforms.inbox import HANDLERS as inbox_handlers

HANDLERS = {}
for suffix, handler in inbox_handlers.items():
    HANDLERS[f"inbox.{suffix}"] = handler
