import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from starlette.datastructures import ImmutableMultiDict

from app.ui.presenters import (
    config_context,
    dashboard_context,
    directories_context,
    integration_header_context,
    llm_profiles_context,
    log_detail_context,
    log_list_context,
    queue_context,
    scripts_list_context,
)
from app.config import reload_config
from app.ui.yaml_rw import (
    ConfigValidationError,
    delete_llm_profile,
    delete_script,
    is_dirty,
    save_raw_yaml,
    update_directories,
    update_integration_settings,
    update_llm_profile,
    update_script,
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=True)
_SENTINEL = Path(__file__).resolve().parent.parent.parent / ".assistant-restart"

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _supervisor_active() -> bool:
    return os.environ.get("ASSISTANT_SUPERVISOR") == "1"


def _render_error(error_msg: str) -> HTMLResponse:
    template = _env.get_template("partials/_error.html")
    return HTMLResponse(template.render(error=error_msg), status_code=422)


def _render_oob_banner() -> str:
    template = _env.get_template("partials/_config_banner.html")
    return template.render(config_dirty=is_dirty(), supervisor_active=_supervisor_active())


def _render_config_page() -> HTMLResponse:
    """Render the config page. Presenters read from disk, so this always
    reflects the file as written — no reload needed."""
    template = _env.get_template("config.html")
    return HTMLResponse(template.render(**config_context(), supervisor_active=_supervisor_active()))


def _render_llm_section() -> HTMLResponse:
    template = _env.get_template("partials/_llm_section.html")
    content = template.render(**llm_profiles_context())
    return HTMLResponse(content + _render_oob_banner())


def _render_scripts_section() -> HTMLResponse:
    template = _env.get_template("partials/_scripts_section.html")
    content = template.render(**scripts_list_context())
    return HTMLResponse(content + _render_oob_banner())


def _render_directories_section() -> HTMLResponse:
    template = _env.get_template("partials/_directories_section.html")
    content = template.render(**directories_context())
    return HTMLResponse(content + _render_oob_banner())


def _render_integration_header(index: int) -> HTMLResponse:
    template = _env.get_template("partials/_integration_header.html")
    content = template.render(**integration_header_context(index))
    return HTMLResponse(content + _render_oob_banner())


def _parse_parameters(raw: str) -> dict[str, Any]:
    """Parse a YAML string into a dict for LLM parameters."""
    if not raw or not raw.strip():
        return {}
    from io import StringIO

    from ruamel.yaml import YAML

    y = YAML(typ="safe")
    result = y.load(StringIO(raw))
    if not isinstance(result, dict):
        raise ConfigValidationError("Parameters must be a YAML mapping")
    return result


def _parse_schedule(schedule_type: str, schedule_value: str) -> dict[str, str] | None:
    if schedule_type == "none" or not schedule_value.strip():
        return None
    if schedule_type == "every":
        return {"every": schedule_value.strip()}
    if schedule_type == "cron":
        return {"cron": schedule_value.strip()}
    return None


# ---------------------------------------------------------------------------
# GET endpoints (read-only pages)
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard():
    template = _env.get_template("dashboard.html")
    return template.render(
        **dashboard_context(),
        config_dirty=is_dirty(),
        supervisor_active=_supervisor_active(),
    )


@router.get("/config", response_class=HTMLResponse)
async def config_page():
    template = _env.get_template("config.html")
    return template.render(**config_context(), supervisor_active=_supervisor_active())


@router.get("/queue", response_class=HTMLResponse)
async def queue_page():
    template = _env.get_template("queue.html")
    return template.render(
        **queue_context(),
        config_dirty=is_dirty(),
        supervisor_active=_supervisor_active(),
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page():
    template = _env.get_template("logs.html")
    ctx = log_list_context()
    ctx["date"] = None
    ctx["content"] = None
    ctx["config_dirty"] = is_dirty()
    return template.render(**ctx, supervisor_active=_supervisor_active())


@router.get("/logs/{date}", response_class=HTMLResponse)
async def log_detail(date: str) -> str:
    template = _env.get_template("logs.html")
    return template.render(
        **log_detail_context(date),
        config_dirty=is_dirty(),
        supervisor_active=_supervisor_active(),
    )


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------


@router.post("/system/restart", response_class=HTMLResponse)
async def restart():
    _SENTINEL.touch()
    log.info("Restart requested via UI")
    template = _env.get_template("restart.html")
    return HTMLResponse(template.render())


# ---------------------------------------------------------------------------
# POST/DELETE endpoints (config editing)
# ---------------------------------------------------------------------------


@router.post("/config/llms/{name}", response_class=HTMLResponse)
async def update_llm(name: str, request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        profile_name = name
        if name == "_new":
            profile_name = str(form.get("profile_name", "")).strip()
            if not profile_name:
                return _render_error("Profile name is required")

        updates: dict[str, Any] = {}
        if form.get("base_url"):
            updates["base_url"] = form["base_url"]
        if form.get("model"):
            updates["model"] = form["model"]
        if form.get("parameters"):
            updates["parameters"] = _parse_parameters(str(form["parameters"]))
        elif "parameters" in form:
            updates["parameters"] = {}

        if not updates.get("model"):
            return _render_error("Model is required")

        update_llm_profile(profile_name, updates)
        reload_config()
    except ConfigValidationError as exc:
        return _render_error(str(exc))

    return _render_llm_section()


@router.delete("/config/llms/{name}", response_class=HTMLResponse)
async def remove_llm(name: str) -> HTMLResponse:
    try:
        delete_llm_profile(name)
        reload_config()
    except ConfigValidationError as exc:
        return _render_error(str(exc))

    return _render_llm_section()


@router.post("/config/directories", response_class=HTMLResponse)
async def update_dirs(request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        updates = {
            "notes": str(form.get("notes", "")).strip(),
            "task_queue": str(form.get("task_queue", "")).strip(),
            "logs": str(form.get("logs", "")).strip(),
            "custom_integrations": str(form.get("custom_integrations", "")).strip(),
        }
        if not updates.get("task_queue"):
            return _render_error("Task queue path is required")
        if not updates.get("logs"):
            return _render_error("Logs path is required")
        update_directories(updates)
        reload_config()
    except ConfigValidationError as exc:
        return _render_error(str(exc))

    return _render_directories_section()


@router.post("/config/integrations/{index}/settings", response_class=HTMLResponse)
async def update_integration(index: int, request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        updates: dict[str, Any] = {}
        schedule = _parse_schedule(
            str(form.get("schedule_type", "none")),
            str(form.get("schedule_value", "")),
        )
        updates["schedule"] = schedule
        if form.get("llm"):
            updates["llm"] = form["llm"]
        update_integration_settings(index, updates)
        reload_config()
    except ConfigValidationError as exc:
        return _render_error(str(exc))

    return _render_integration_header(index)


def _build_script_updates(form: ImmutableMultiDict[str, Any]) -> dict[str, Any]:
    """Extract script fields from form data into an updates dict."""
    updates: dict[str, Any] = {"shell": str(form.get("shell", "")).strip()}
    updates["description"] = form.get("description", "")
    updates["timeout"] = int(form["timeout"]) if form.get("timeout") else 120
    updates["inputs"] = (
        [s.strip() for s in form["inputs"].split(",") if s.strip()]
        if form.get("inputs")
        else []
    )
    updates["output"] = form.get("output", "") or None
    updates["on_output"] = form.get("on_output", "human_log")
    updates["reversible"] = "reversible" in form
    return updates


@router.post("/config/scripts/{name}", response_class=HTMLResponse)
async def update_script_endpoint(name: str, request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        script_name = name
        if name == "_new":
            script_name = str(form.get("script_name", "")).strip()
            if not script_name:
                return _render_error("Script name is required")

        updates = _build_script_updates(form)
        if not updates["shell"]:
            return _render_error("Shell command is required")

        update_script(script_name, updates)
        reload_config()
    except ConfigValidationError as exc:
        return _render_error(str(exc))

    return _render_scripts_section()


@router.delete("/config/scripts/{name}", response_class=HTMLResponse)
async def remove_script(name: str) -> HTMLResponse:
    try:
        delete_script(name)
        reload_config()
    except ConfigValidationError as exc:
        return _render_error(str(exc))

    return _render_scripts_section()


@router.post("/integrations/{integration_id}/run", response_class=HTMLResponse)
async def trigger_integration(integration_id: str) -> HTMLResponse:
    from app.main import _run_integration
    try:
        result = _run_integration(integration_id)
        task_ids = result.get("task_ids", [])
        msg = f"Enqueued {len(task_ids)} tasks: {', '.join(task_ids)}"
        return HTMLResponse(f'<div class="text-xs text-success mt-2">{msg}</div>')
    except Exception as exc:
        return _render_error(str(exc))


@router.post("/config/yaml", response_class=HTMLResponse)
async def save_raw(request: Request) -> HTMLResponse:
    form = await request.form()
    yaml_content = str(form.get("yaml_content", ""))
    try:
        save_raw_yaml(yaml_content)
        reload_config()
    except ConfigValidationError as exc:
        return _render_error(str(exc))

    success_content = '<div class="alert alert-success mb-4"><span>Config saved.</span></div>'
    return HTMLResponse(success_content + _render_oob_banner())
