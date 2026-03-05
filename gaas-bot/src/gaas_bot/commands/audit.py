"""Audit commands — docs, refactor, tests."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import click

from gaas_bot.core import agent, git, templates
from gaas_bot.core.github import (
    build_github_context,
    create_issue,
)
from gaas_bot.models.audit import AuditReport


QUALITY_TOOLS = [
    {
        "name": "mypy",
        "cmd": ["uv", "run", "mypy", "app/", "packages/", "--ignore-missing-imports"],
    },
    {
        "name": "complexipy",
        "cmd": ["uv", "run", "complexipy", "app/", "packages/", "--max-complexity", "15"],
    },
    {
        "name": "radon",
        "cmd": ["uv", "run", "radon", "cc", "app/", "-a", "-nc"],
    },
    {
        "name": "vulture",
        "cmd": ["uv", "run", "vulture", "app/", "packages/", "--min-confidence", "80"],
    },
    {
        "name": "ruff",
        "cmd": ["uv", "run", "ruff", "check", "app/", "packages/", "tests/"],
    },
    {
        "name": "bandit",
        "cmd": ["uv", "run", "bandit", "-r", "app/", "-q"],
    },
]


def run_quality_tools(worktree_dir: Path) -> str:
    """Run each quality tool and collect output."""
    sections = []
    for tool in QUALITY_TOOLS:
        name = tool["name"]
        click.echo(f"Running {name}...")
        result = subprocess.run(
            tool["cmd"],
            cwd=worktree_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        sections.append(f"## {name}\n\n```\n{output}\n```")
    return "\n\n".join(sections)


def file_issues(
    report: AuditReport, *, owner: str, repo: str, dry_run: bool,
) -> None:
    """Create GitHub issues from an audit report, or print them in dry-run mode."""
    if not report.findings:
        click.echo("Audit produced no findings.")
        return

    if dry_run:
        click.echo(f"\n--- dry run: {len(report.findings)} finding(s) ---\n")
        for i, finding in enumerate(report.findings, 1):
            click.echo(f"[{i}] {finding.title}")
            click.echo(f"    Labels: {', '.join(finding.labels)}")
            click.echo(f"    Body:\n{finding.body}\n")
        return

    gh_ctx = build_github_context()
    click.echo(f"\nCreating {len(report.findings)} issue(s)...\n")
    for finding in report.findings:
        url = create_issue(
            gh_ctx.gh, owner, repo,
            title=finding.title,
            body=finding.body,
            labels=finding.labels,
        )
        click.echo(f"  Created: {url}")


DEFAULT_LIMIT = 3


def run_audit(
    template_name: str,
    template_ctx: dict,
    *,
    allowed_tools: list[str],
    owner: str,
    repo: str,
    dry_run: bool,
) -> None:
    """Shared audit runner: agent exploration -> structured output -> issue creation."""
    worktree_dir = git.create_worktree(detach=True)
    try:
        prompt = templates.render(template_name, template_ctx)
        click.echo(f"\n--- {template_name}: starting Claude agent ---\n")
        result, _ = asyncio.run(agent.run_agent(
            prompt,
            cwd=worktree_dir,
            allowed_tools=allowed_tools,
            max_turns=30,
            output_model=AuditReport,
        ))

        report = AuditReport.model_validate(result)
        file_issues(report, owner=owner, repo=repo, dry_run=dry_run)
    finally:
        git.remove_worktree(worktree_dir)


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------

@click.group()
def audit() -> None:
    """Audit the codebase for docs drift, refactoring opportunities, and test gaps."""


@audit.command()
@click.option("--owner", default="gregology", help="Repository owner")
@click.option("--repo", default="GaaS", help="Repository name")
@click.option("--dry-run", is_flag=True, help="Preview findings without creating issues")
@click.option("--limit", default=DEFAULT_LIMIT, type=int, help="Max number of findings")
def docs(owner: str, repo: str, dry_run: bool, limit: int) -> None:
    """Check documentation for drift from the codebase."""
    run_audit(
        "audit_docs.md.j2", {"max_findings": limit},
        allowed_tools=["Read", "Glob", "Grep"],
        owner=owner, repo=repo, dry_run=dry_run,
    )


@audit.command("refactor")
@click.option("--owner", default="gregology", help="Repository owner")
@click.option("--repo", default="GaaS", help="Repository name")
@click.option("--dry-run", is_flag=True, help="Preview findings without creating issues")
@click.option("--limit", default=DEFAULT_LIMIT, type=int, help="Max number of findings")
def refactor(owner: str, repo: str, dry_run: bool, limit: int) -> None:
    """Run code quality tools and identify refactoring opportunities."""
    worktree_dir = git.create_worktree(detach=True)
    try:
        tool_output = run_quality_tools(worktree_dir)
        prompt = templates.render("audit_refactor.md.j2", {"tool_output": tool_output, "max_findings": limit})
        click.echo("\n--- audit refactor: starting Claude agent ---\n")
        result, _ = asyncio.run(agent.run_agent(
            prompt,
            cwd=worktree_dir,
            allowed_tools=["Read", "Glob", "Grep"],
            max_turns=30,
            output_model=AuditReport,
        ))

        report = AuditReport.model_validate(result)
        file_issues(report, owner=owner, repo=repo, dry_run=dry_run)
    finally:
        git.remove_worktree(worktree_dir)


@audit.command()
@click.option("--owner", default="gregology", help="Repository owner")
@click.option("--repo", default="GaaS", help="Repository name")
@click.option("--dry-run", is_flag=True, help="Preview findings without creating issues")
@click.option("--limit", default=DEFAULT_LIMIT, type=int, help="Max number of findings")
def tests(owner: str, repo: str, dry_run: bool, limit: int) -> None:
    """Audit test coverage and test-risk alignment."""
    run_audit(
        "audit_tests.md.j2", {"max_findings": limit},
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        owner=owner, repo=repo, dry_run=dry_run,
    )
