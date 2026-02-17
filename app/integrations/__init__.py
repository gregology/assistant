from app.integrations import email as _email_mod
from app.integrations import github as _github_mod

HANDLERS: dict[str, callable] = {}

ENTRY_TASKS: dict[str, str] = {
    "email": "email.check",
    "github": "github.update_prs",
}


def _register(prefix: str, module) -> None:
    for suffix, handler in module.HANDLERS.items():
        HANDLERS[f"{prefix}.{suffix}"] = handler


_register("email", _email_mod)
_register("github", _github_mod)
