"""Git helpers — worktrees, diff, commit, push."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed with a meaningful error message."""


def _run_git(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    secrets: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command, raising GitError with redacted stderr on failure."""
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        stderr = result.stderr
        for secret in secrets or []:
            if secret:
                stderr = stderr.replace(secret, "***")
        raise GitError(f"Command {cmd[0:2]} failed (exit {result.returncode}): {stderr.strip()}")
    return result


def repo_root() -> Path:
    """Return the root of the git repository containing this package."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def create_worktree(branch: str | None = None, *, detach: bool = False) -> Path:
    """Create a temporary git worktree from origin/main.

    If branch is given, creates or resets that branch to origin/main.
    If detach is True, creates a detached HEAD worktree (for read-only audits).
    """
    root = repo_root()
    subprocess.run(
        ["git", "fetch", "origin", "main"],
        cwd=root, check=True, capture_output=True,
    )

    worktree_dir = Path(tempfile.mkdtemp(prefix="gaas-bot-"))

    if detach:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_dir), "origin/main"],
            cwd=root, check=True, capture_output=True,
        )
    elif branch:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=root, capture_output=True,
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "branch", "-f", branch, "origin/main"],
                cwd=root, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "worktree", "add", str(worktree_dir), branch],
                cwd=root, check=True, capture_output=True,
            )
        else:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(worktree_dir), "origin/main"],
                cwd=root, check=True, capture_output=True,
            )
    else:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_dir), "origin/main"],
            cwd=root, check=True, capture_output=True,
        )

    print(f"Created worktree at {worktree_dir}")
    return worktree_dir


def remove_worktree(worktree_dir: Path) -> None:
    """Remove a temporary worktree and its directory."""
    root = repo_root()
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=root, capture_output=True,
    )
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir)
    print("Worktree cleaned up.")


def get_diff(worktree_dir: Path) -> str:
    """Return the diff of the worktree against origin/main."""
    result = subprocess.run(
        ["git", "diff", "origin/main"],
        cwd=worktree_dir, capture_output=True, text=True,
    )
    return result.stdout


def rename_branch(worktree_dir: Path, new_name: str) -> None:
    """Rename the current branch in a worktree."""
    subprocess.run(
        ["git", "branch", "-M", new_name],
        cwd=worktree_dir, check=True, capture_output=True,
    )


def commit_and_push(
    worktree_dir: Path,
    branch: str,
    commit_message: str,
    *,
    bot_name: str,
    bot_email: str,
    token: str,
    owner: str,
    repo: str,
) -> bool:
    """Stage, commit, and push changes. Returns True if changes were pushed."""
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_dir, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        print("No changes to commit.")
        return False

    _run_git(["git", "add", "-A"], cwd=worktree_dir)

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": bot_name,
        "GIT_AUTHOR_EMAIL": bot_email,
        "GIT_COMMITTER_NAME": bot_name,
        "GIT_COMMITTER_EMAIL": bot_email,
    }
    _run_git(
        ["git", "commit", "-m", commit_message],
        cwd=worktree_dir, env=env,
    )

    push_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    _run_git(
        ["git", "push", push_url, f"HEAD:{branch}", "--force"],
        cwd=worktree_dir, secrets=[token],
    )
    print(f"Pushed branch {branch}")
    return True
