"""gaas-bot CLI — maintenance automation for GaaS."""

from pathlib import Path

import click

from gaas_bot.commands.audit import audit
from gaas_bot.commands.resolve import resolve

PACKAGE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = PACKAGE_DIR.parent.parent / ".env"


@click.group()
@click.version_option(package_name="gaas-bot")
def cli() -> None:
    """gaas-bot: maintenance automation for GaaS."""
    try:
        from dotenv import load_dotenv
        load_dotenv(DOTENV_PATH)
    except ImportError:
        pass


cli.add_command(resolve)
cli.add_command(audit)
