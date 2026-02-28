import logging
from .platforms.inbox.act import handle as inbox_act_handle

log = logging.getLogger(__name__)

def draft_reply(task: dict):
    """Global handler for email.draft_reply."""
    # We leverage the existing inbox.act handler by transforming the task
    payload = task["payload"]
    inputs = payload.get("inputs", {})
    body = inputs.get("body")
    
    if not body:
        log.warning("email.draft_reply: 'body' input is missing")
        return
    
    # Reconstruct a task that inbox.act.handle understands
    inbox_task = {
        "payload": {
            **payload,
            "actions": [{"draft_reply": body}]
        }
    }
    return inbox_act_handle(inbox_task)
