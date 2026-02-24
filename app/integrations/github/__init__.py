from app.integrations.github.platforms.pull_requests import HANDLERS as pr_handlers
from app.integrations.github.platforms.issues import HANDLERS as issues_handlers

HANDLERS = {}
for suffix, handler in pr_handlers.items():
    HANDLERS[f"pull_requests.{suffix}"] = handler
for suffix, handler in issues_handlers.items():
    HANDLERS[f"issues.{suffix}"] = handler
