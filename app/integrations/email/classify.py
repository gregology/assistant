import logging
import secrets
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import config
from app.llm import LLMConversation
from .mail import Mailbox
from .store import EmailStore

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

CLASSIFY_SCHEMA = {
    "properties": {
        "human": {"type": "number"},
        "robot": {"type": "number"},
        "requires_response": {"type": "number"},
        "requires_action": {"type": "number"},
        "urgency": {"type": "number"},
        "user_agreement_update": {"type": "number"},
    },
    "required": ["human", "robot", "requires_response", "requires_action", "urgency", "user_agreement_update"],
}


def _render_prompt(email) -> str:
    template = jinja_env.get_template("classify_email.jinja")
    return template.render(
        beginning_salt=secrets.token_hex(16),
        end_salt=secrets.token_hex(16),
        email=email,
    )


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name)
    uid = task["payload"]["uid"]
    log.info("email.classify: uid=%s (integration=%s)", uid, integration_name)

    with Mailbox(
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        email = mb.get_email(uid)

    prompt = _render_prompt(email)
    log.info("email.classify prompt:\n%s", prompt)
    conversation = LLMConversation(
        model=integration.llm,
        system="Disable internal monologue. Answer directly. Respond with JSON.",
    )
    classification = conversation.message(prompt=prompt, schema=CLASSIFY_SCHEMA)

    log.info("email.classify: uid=%s result=%s", uid, classification)

    notes_dir = config.directories.notes
    store = EmailStore(path=notes_dir / "emails" / integration.username)
    store.update(uid, classification=classification)
