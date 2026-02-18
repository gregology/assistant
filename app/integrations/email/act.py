import logging

from app.config import config
from .mail import Mailbox

log = logging.getLogger(__name__)

SIMPLE_ACTIONS = {"archive", "spam", "unsubscribe"}


def _execute_action(email, action) -> None:
    if isinstance(action, str):
        if action not in SIMPLE_ACTIONS:
            log.warning("email.act: unknown action %r, skipping", action)
            return
        getattr(email, action)()
    elif isinstance(action, dict):
        if "draft_reply" in action:
            email.draft_reply(action["draft_reply"])
        else:
            log.warning("email.act: unknown action dict %r, skipping", action)


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name)
    uid = task["payload"]["uid"]
    actions = task["payload"]["actions"]
    log.info("email.act: uid=%s actions=%s (integration=%s)", uid, actions, integration_name)

    with Mailbox(
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        email = mb.get_email(uid)

        for action in actions:
            _execute_action(email, action)
