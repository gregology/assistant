import logging
import secrets
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.llm import LLMConversation
from app.mail import Mailbox
from app.store import EmailStore

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


def _render_prompt(email_contents_clean: str) -> str:
    template = jinja_env.get_template("classify_email.jinja")
    return template.render(
        beginning_salt=secrets.token_hex(16),
        end_salt=secrets.token_hex(16),
        email_contents_clean=email_contents_clean,
    )


def handle(task: dict):
    uid = task["payload"]["uid"]
    log.info("classify_email: uid=%s", uid)

    with Mailbox() as mb:
        email = mb.get_email(uid)

    prompt = _render_prompt(email.contents_clean)
    conversation = LLMConversation(
        model="fast",
        system="Disable internal monologue. Answer directly. Respond with JSON."
    )
    classification = conversation.message(prompt=prompt, schema=CLASSIFY_SCHEMA)

    log.info("classify_email: uid=%s result=%s", uid, classification)

    store = EmailStore()
    store.update(uid, classification=classification)
