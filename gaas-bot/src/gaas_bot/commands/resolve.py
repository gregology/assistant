"""Resolve a GitHub issue using a declarative Claude agent pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import click
from pydantic import BaseModel

from gaas_bot.core import agent, git, templates
from gaas_bot.core.github import (
    GitHubContext,
    build_github_context,
    create_pull_request,
    fetch_comments,
    fetch_issue,
    post_comment,
)
from gaas_bot.models.resolve import (
    CommentResult,
    EvalResult,
    PRResult,
    TriageResult,
)


# ---------------------------------------------------------------------------
# Stage definition
# ---------------------------------------------------------------------------

@dataclass
class Stage:
    template: str
    session: str  # "new" | "resume:<stage_name>" | "fork:<stage_name>"
    output: type[BaseModel] | None
    tools: list[str]
    max_turns: int
    route: Callable[[dict], str | None]
    pre: Callable[[dict, Path], None] | None = None
    post: Callable[[str, dict, GitHubContext, Path], None] | None = None


# ---------------------------------------------------------------------------
# Route functions
# ---------------------------------------------------------------------------

def triage_route(ctx: dict) -> str | None:
    d = ctx["triage"]["decision"]
    if d in ("ask", "propose"):
        return "comment_draft"
    return "implementation"


def eval_route(ctx: dict) -> str | None:
    if ctx["evaluation"]["decision"] == "needs_context":
        return "eval_comment"
    return "docs_review"


# ---------------------------------------------------------------------------
# Pre/post hooks
# ---------------------------------------------------------------------------

def inject_diff(ctx: dict, worktree_dir: Path) -> None:
    ctx["diff"] = git.get_diff(worktree_dir)


def post_comment_action(
    stage_name: str, ctx: dict, gh_ctx: GitHubContext, worktree_dir: Path,
) -> None:
    comment_text = ctx[stage_name]["comment"]
    url = post_comment(gh_ctx.gh, ctx["owner"], ctx["repo"], ctx["issue_number"], comment_text)
    print(f"Comment posted: {url}")


def post_pr_action(
    stage_name: str, ctx: dict, gh_ctx: GitHubContext, worktree_dir: Path,
) -> None:
    pr = ctx["pr_draft"]

    git.rename_branch(worktree_dir, pr["branch_name"])

    if git.commit_and_push(
        worktree_dir,
        pr["branch_name"],
        pr["commit_message"],
        bot_name=gh_ctx.bot_name,
        bot_email=gh_ctx.bot_email,
        token=gh_ctx.token,
        owner=ctx["owner"],
        repo=ctx["repo"],
    ):
        pr_url = create_pull_request(
            gh_ctx.gh, ctx["owner"], ctx["repo"],
            pr["branch_name"], ctx["issue_number"],
            pr["pr_body"], pr["commit_message"],
        )
        print(f"Pull request created: {pr_url}")
    else:
        print("No changes were made. Skipping PR creation.")


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

STAGES: dict[str, Stage] = {
    "triage": Stage(
        template="resolve_triage.md.j2",
        session="new",
        output=TriageResult,
        tools=["Read", "Glob", "Grep"],
        max_turns=20,
        route=triage_route,
    ),
    "comment_draft": Stage(
        template="resolve_comment.md.j2",
        session="resume:triage",
        output=CommentResult,
        tools=["Read", "Glob", "Grep"],
        max_turns=15,
        route=lambda ctx: None,
        post=post_comment_action,
    ),
    "implementation": Stage(
        template="resolve_implement.md.j2",
        session="new",
        output=None,
        tools=["Read", "Glob", "Grep", "Write", "Edit", "Bash"],
        max_turns=50,
        route=lambda ctx: "evaluation",
    ),
    "evaluation": Stage(
        template="resolve_eval.md.j2",
        session="new",
        output=EvalResult,
        tools=["Read", "Glob", "Grep"],
        max_turns=15,
        route=eval_route,
        pre=inject_diff,
    ),
    "eval_comment": Stage(
        template="resolve_eval_comment.md.j2",
        session="resume:evaluation",
        output=CommentResult,
        tools=["Read", "Glob", "Grep"],
        max_turns=10,
        route=lambda ctx: None,
        post=post_comment_action,
    ),
    "docs_review": Stage(
        template="resolve_docs.md.j2",
        session="resume:implementation",
        output=None,
        tools=["Read", "Glob", "Grep", "Write", "Edit"],
        max_turns=20,
        route=lambda ctx: "pr_draft",
    ),
    "pr_draft": Stage(
        template="resolve_pr.md.j2",
        session="resume:implementation",
        output=PRResult,
        tools=["Read", "Glob", "Grep"],
        max_turns=15,
        route=lambda ctx: None,
        post=post_pr_action,
    ),
}

FIRST_STAGE = "triage"


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------

async def run_stage(
    name: str,
    stage: Stage,
    ctx: dict,
    worktree_dir: Path,
    sessions: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Run a single pipeline stage."""
    prompt = templates.render(stage.template, ctx)

    # Resolve session strategy
    resume_id = None
    fork = False
    if stage.session.startswith("resume:"):
        ref = stage.session.split(":", 1)[1]
        resume_id = sessions.get(ref)
        if not resume_id:
            click.echo(f"Warning: no session '{ref}' to resume, starting new session", err=True)
    elif stage.session.startswith("fork:"):
        ref = stage.session.split(":", 1)[1]
        resume_id = sessions.get(ref)
        fork = True
        if not resume_id:
            click.echo(f"Warning: no session '{ref}' to fork, starting new session", err=True)

    click.echo(f"\n--- {name}: starting Claude agent ---\n")

    result, session_id = await agent.run_agent(
        prompt,
        cwd=worktree_dir,
        allowed_tools=stage.tools,
        max_turns=stage.max_turns,
        output_model=stage.output,
        resume=resume_id,
        fork_session=fork,
    )

    click.echo(f"\n--- {name}: complete ---")
    return result, session_id


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(gh_ctx: GitHubContext, initial_ctx: dict, worktree_dir: Path) -> None:
    """Execute the resolve pipeline from triage to completion."""
    ctx = dict(initial_ctx)
    sessions: dict[str, str] = {}
    current: str | None = FIRST_STAGE

    while current is not None:
        stage = STAGES[current]

        if stage.pre is not None:
            stage.pre(ctx, worktree_dir)

        result, session_id = asyncio.run(
            run_stage(current, stage, ctx, worktree_dir, sessions)
        )

        if session_id:
            sessions[current] = session_id
        if result is not None:
            ctx[current] = result if isinstance(result, dict) else result

        if stage.post is not None:
            stage.post(current, ctx, gh_ctx, worktree_dir)

        next_stage = stage.route(ctx)
        if next_stage is not None and next_stage not in STAGES:
            click.echo(f"Unknown stage '{next_stage}' returned by route, stopping.", err=True)
            break
        current = next_stage


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command()
@click.option("--issue", required=True, type=int, help="Issue number to resolve")
@click.option("--owner", default="gregology", help="Repository owner")
@click.option("--repo", default="GaaS", help="Repository name")
def resolve(issue: int, owner: str, repo: str) -> None:
    """Resolve a GitHub issue using Claude Code."""
    gh_ctx = build_github_context()
    branch = f"resolve_issue_{issue}"

    click.echo(f"Fetching issue #{issue}...")
    issue_data = fetch_issue(gh_ctx.gh, owner, repo, issue)
    comments = fetch_comments(gh_ctx.gh, owner, repo, issue)

    worktree_dir = git.create_worktree(branch)

    try:
        initial_ctx = {
            "owner": owner,
            "repo": repo,
            "issue_number": issue,
            "issue_title": issue_data["title"],
            "issue_body": issue_data["body"],
            "issue_author": issue_data["author"],
            "issue_state": issue_data["state"],
            "issue_labels": issue_data["labels"],
            "comments": comments,
        }
        run_pipeline(gh_ctx, initial_ctx, worktree_dir)
    finally:
        git.remove_worktree(worktree_dir)
