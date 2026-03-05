import logging
import secrets
from datetime import datetime, UTC
from pathlib import Path

import frontmatter

from gaas_sdk import runtime
from gaas_sdk.classify import build_schema, make_jinja_env
from gaas_sdk.models import ClassificationConfig
from gaas_sdk.task import TaskRecord
from .const import DEFAULT_CLASSIFICATIONS
from .store import EmailStore

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = make_jinja_env(TEMPLATES_DIR)

MAX_BODY_CHARS = 5_000


def _render_prompt(email, classifications: dict[str, ClassificationConfig]) -> str:
    body = email.contents_clean
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n... (body truncated)"
    template = jinja_env.get_template("classify.jinja")
    return template.render(
        salt=secrets.token_hex(4).upper(),
        email=email,
        contents_clean=body,
        classifications=classifications,
    )


def handle(task: TaskRecord):
    from ...mail import Mailbox

    integration_id = task["payload"]["integration"]
    integration = runtime.get_integration(integration_id)
    platform = runtime.get_platform(integration_id, "inbox")
    uid = task["payload"]["uid"]
    log.info("email.inbox.classify: uid=%s (integration=%s)", uid, integration_id)

    classifications = platform.classifications or DEFAULT_CLASSIFICATIONS
    llm_config = runtime.get_llm_config(integration.llm)

    notes_dir = runtime.get_notes_dir()
    store = EmailStore(path=notes_dir / "emails" / integration.name)

    with Mailbox(
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        email = mb.get_email(uid)

    message_id = email._message_id or f"imap_{uid}"

    filepath = store.find_by_message_id(message_id)
    existing_cls = {}
    if filepath:
        post = frontmatter.load(filepath)
        existing_cls = post.metadata.get("classification", {})

    if all(k in existing_cls for k in classifications):
        log.info("email.inbox.classify: uid=%s all classifications present, skipping LLM", uid)
    else:
        prompt = _render_prompt(email, classifications)
        log.info("email.inbox.classify prompt:\n%s", prompt)
        conversation = runtime.create_llm_conversation(
            model=integration.llm,
            system="Disable internal monologue. Answer directly. Respond with JSON.",
        )
        schema = build_schema(classifications)
        classification = conversation.message(prompt=prompt, schema=schema)
        log.info("email.inbox.classify: uid=%s result=%s", uid, classification)

        classified_by = {
            "model": llm_config.model,
            "profile": integration.llm,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        store.update(message_id, classification=classification, classified_by=classified_by)

    runtime.enqueue({
        "type": "email.inbox.evaluate",
        "integration": integration_id,
        "message_id": message_id,
    }, priority=7)
    log.info("email.inbox.classify: queued evaluate for uid=%s", uid)
