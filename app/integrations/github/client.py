from __future__ import annotations

import json
import logging
import subprocess

log = logging.getLogger(__name__)


class GitHubClient:
    def get_pr(self, org: str, repo: str, number: int) -> dict:
        result = self._gh_api(f"repos/{org}/{repo}/pulls/{number}")
        merged = result.get("merged", False)
        state = result.get("state", "unknown")
        if merged:
            status = "merged"
        elif state == "closed":
            status = "closed"
        else:
            status = "open"
        return {
            "org": org,
            "repo": repo,
            "number": number,
            "title": result.get("title", ""),
            "author": result.get("user", {}).get("login", ""),
            "status": status,
        }

    def get_pr_detail(self, org: str, repo: str, number: int) -> dict:
        result = self._gh_api(f"repos/{org}/{repo}/pulls/{number}")
        return {
            "title": result.get("title", ""),
            "body": result.get("body", "") or "",
            "author": result.get("user", {}).get("login", ""),
            "additions": result.get("additions", 0),
            "deletions": result.get("deletions", 0),
            "changed_files": result.get("changed_files", 0),
        }

    def get_pr_diff(self, org: str, repo: str, number: int) -> str:
        cmd = [
            "gh", "api", f"repos/{org}/{repo}/pulls/{number}",
            "--method", "GET",
            "-H", "Accept: application/vnd.github.v3.diff",
        ]
        log.info("gh api: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            log.error("gh api failed: %s", proc.stderr)
            raise RuntimeError(f"gh api failed (exit {proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout

    def assigned_prs(self) -> list[dict]:
        result = self._gh_api(
            "search/issues",
            method="GET",
            params={
                "q": "is:pr is:open review-requested:@me",
                "per_page": "100",
            },
        )
        prs = []
        for item in result.get("items", []):
            repo_url = item.get("repository_url", "")
            segments = repo_url.rstrip("/").split("/")
            if len(segments) < 2:
                log.warning("Cannot parse org/repo from repository_url: %s", repo_url)
                continue
            prs.append({
                "org": segments[-2],
                "repo": segments[-1],
                "number": item["number"],
                "title": item["title"],
                "author": item.get("user", {}).get("login", ""),
            })
        log.info("assigned_prs: found %d PRs", len(prs))
        return prs

    def _gh_api(self, endpoint: str, method: str = "GET", params: dict | None = None) -> dict:
        cmd = ["gh", "api", endpoint, "--method", method]
        for key, value in (params or {}).items():
            cmd.extend(["-f", f"{key}={value}"])
        log.info("gh api: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            log.error("gh api failed: %s", proc.stderr)
            raise RuntimeError(f"gh api failed (exit {proc.returncode}): {proc.stderr.strip()}")
        return json.loads(proc.stdout)
