import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

import frontmatter
from jinja2 import Environment, FileSystemLoader

from app import queue
from app.config import ClassificationConfig, config
from app.integrations.email.const import DEFAULT_CLASSIFICATIONS
from app.llm import LLMConversation
from .mail import Mailbox
from .store import EmailStore

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

_TYPE_TO_SCHEMA = {
    "confidence": lambda _cls: {"type": "number"},
    "boolean": lambda _cls: {"type": "boolean"},
    "enum": lambda cls: {"type": "string", "enum": cls.values},
}


def _build_schema(classifications: dict[str, ClassificationConfig]) -> dict:
    properties = {}
    for name, cls in classifications.items():
        properties[name] = _TYPE_TO_SCHEMA[cls.type](cls)
    return {
        "properties": properties,
        "required": list(classifications.keys()),
    }


def _render_prompt(email, classifications: dict[str, ClassificationConfig]) -> str:
    template = jinja_env.get_template("classify_email.jinja")
    return template.render(
        beginning_salt=secrets.token_hex(16),
        end_salt=secrets.token_hex(16),
        email=email,
        classifications=classifications,
    )


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name, "email")
    uid = task["payload"]["uid"]
    log.info("email.classify: uid=%s (integration=%s)", uid, integration_name)

    classifications = integration.classifications or DEFAULT_CLASSIFICATIONS
    llm_config = config.llms[integration.llm]

    notes_dir = config.directories.notes
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
        log.info("email.classify: uid=%s all classifications present, skipping LLM", uid)
    else:
        prompt = _render_prompt(email, classifications)
        log.info("email.classify prompt:\n%s", prompt)
        conversation = LLMConversation(
            model=integration.llm,
            system="Disable internal monologue. Answer directly. Respond with JSON.",
        )
        schema = _build_schema(classifications)
        classification = conversation.message(prompt=prompt, schema=schema)
        log.info("email.classify: uid=%s result=%s", uid, classification)

        classified_by = {
            "model": llm_config.model,
            "profile": integration.llm,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        store.update(message_id, classification=classification, classified_by=classified_by)

    queue.enqueue({
        "type": "email.evaluate",
        "integration": integration_name,
        "message_id": message_id,
    }, priority=7)
    log.info("email.classify: queued email.evaluate for uid=%s", uid)
