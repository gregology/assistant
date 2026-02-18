import logging
import operator
import re
import secrets
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app import queue
from app.config import AutomationConfig, ClassificationConfig, config
from app.llm import LLMConversation
from .mail import Mailbox
from .store import EmailStore

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

DEFAULT_CLASSIFICATIONS: dict[str, ClassificationConfig] = {
    "human": ClassificationConfig(prompt="is this a personal email written by a human?"),
    "user_agreement_update": ClassificationConfig(prompt="is this email about a user agreement update?", type="boolean"),
    "requires_response": ClassificationConfig(prompt="does this email require a response?", type="boolean"),
    "priority": ClassificationConfig(prompt="what is the priority of this email?", type="enum", values=["low", "medium", "high", "critical"]),
}

_TYPE_TO_SCHEMA = {
    "confidence": lambda _cls: {"type": "number"},
    "boolean": lambda _cls: {"type": "boolean"},
    "enum": lambda cls: {"type": "string", "enum": cls.values},
}

_OPS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
}

_OP_RE = re.compile(r"^\s*(>=|<=|>|<|==)\s*(\d+\.?\d*)\s*$")


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


def _eval_operator(value: float, expr: str) -> bool:
    match = _OP_RE.match(expr)
    if not match:
        log.warning("Invalid confidence condition: %r", expr)
        return False
    op_fn = _OPS[match.group(1)]
    threshold = float(match.group(2))
    return op_fn(value, threshold)


def _check_condition(value, condition, cls_config: ClassificationConfig) -> bool:
    if cls_config.type == "boolean":
        return value is condition

    if cls_config.type == "confidence":
        if isinstance(condition, (int, float)):
            return value >= condition
        if isinstance(condition, str):
            return _eval_operator(value, condition)
        return False

    if cls_config.type == "enum":
        if isinstance(condition, list):
            return value in condition
        return value == condition

    return False


def _conditions_match(
    when: dict,
    result: dict,
    classifications: dict[str, ClassificationConfig],
) -> bool:
    for name, condition in when.items():
        if name not in result or name not in classifications:
            return False
        if not _check_condition(result[name], condition, classifications[name]):
            return False
    return True


def _evaluate_automations(
    automations: list[AutomationConfig],
    result: dict,
    classifications: dict[str, ClassificationConfig],
) -> list:
    actions = []
    for automation in automations:
        if _conditions_match(automation.when, result, classifications):
            actions.extend(automation.then)
    return actions


def handle(task: dict):
    integration_name = task["payload"]["integration"]
    integration = config.get_integration(integration_name)
    uid = task["payload"]["uid"]
    log.info("email.classify: uid=%s (integration=%s)", uid, integration_name)

    classifications = integration.classifications or DEFAULT_CLASSIFICATIONS

    with Mailbox(
        imap_server=integration.imap_server,
        imap_port=integration.imap_port,
        username=integration.username,
        password=integration.password,
    ) as mb:
        email = mb.get_email(uid)

    prompt = _render_prompt(email, classifications)
    log.info("email.classify prompt:\n%s", prompt)
    conversation = LLMConversation(
        model=integration.llm,
        system="Disable internal monologue. Answer directly. Respond with JSON.",
    )
    schema = _build_schema(classifications)
    classification = conversation.message(prompt=prompt, schema=schema)

    log.info("email.classify: uid=%s result=%s", uid, classification)

    notes_dir = config.directories.notes
    store = EmailStore(path=notes_dir / "emails" / integration.username)
    store.update(uid, classification=classification)

    actions = _evaluate_automations(
        integration.automations, classification, classifications,
    )
    if actions:
        queue.enqueue({
            "type": "email.act",
            "integration": integration_name,
            "uid": uid,
            "actions": actions,
        }, priority=7)
        log.info("email.classify: queued email.act for uid=%s actions=%s", uid, actions)
