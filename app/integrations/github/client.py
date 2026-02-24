from __future__ import annotations

import json
import logging
import subprocess
import time

log = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 1  # seconds; sleeps 1, 2, 4 on retries


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
        return self._run_gh(cmd, timeout=60)

    def active_prs(self, integration) -> list[dict]:
        """Fetch all open PRs currently requiring the user's attention.

        Queries GitHub for PRs where the user is an assignee, requested reviewer,
        or author (non-draft). Optionally includes PRs that mention the user.
        Results are filtered to the configured orgs/repos and deduplicated
        across query types.
        """
        seen: set[tuple[str, str, int]] = set()
        results: list[dict] = []

        base_queries = [
            "is:pr is:open assignee:@me",
            "is:pr is:open review-requested:@me",
            "is:pr is:open author:@me draft:false",
        ]
        if integration.include_mentions:
            base_queries.append("is:pr is:open mentions:@me")

        scopes = self._scope_qualifiers(integration)
        for base_query in base_queries:
            for scope in scopes:
                query = f"{base_query} {scope}".strip()
                for item in self._search_prs(query):
                    key = (item["org"], item["repo"], item["number"])
                    if key not in seen:
                        seen.add(key)
                        results.append(item)

        log.info("active_prs: found %d unique PRs across all queries", len(results))
        return results

    def _scope_qualifiers(self, integration) -> list[str]:
        """Build scope qualifiers from the integration's org/repo config.

        Returns a list of qualifier strings to append to each search query
        (e.g. ["org:myorg", "repo:other/repo"]). Returns [""] — one empty
        qualifier — when no orgs or repos are configured, meaning no filter.
        """
        qualifiers = []
        for org in (integration.orgs or []):
            qualifiers.append(f"org:{org}")
        for repo in (integration.repos or []):
            qualifiers.append(f"repo:{repo}")
        return qualifiers or [""]

    def _search_prs(self, query: str) -> list[dict]:
        """Execute a GitHub search/issues query and return parsed PR dicts."""
        result = self._gh_api(
            "search/issues",
            params={"q": query, "per_page": "100"},
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
        log.info("_search_prs(%r): found %d PRs", query, len(prs))
        return prs

    def _gh_api(self, endpoint: str, method: str = "GET", params: dict | None = None) -> dict:
        cmd = ["gh", "api", endpoint, "--method", method]
        for key, value in (params or {}).items():
            cmd.extend(["-f", f"{key}={value}"])
        return json.loads(self._run_gh(cmd, timeout=30))

    def _run_gh(self, cmd: list[str], *, timeout: int = 30) -> str:
        """Run a gh CLI command with retry and exponential backoff."""
        last_err: RuntimeError | None = None
        for attempt in range(MAX_RETRIES + 1):
            log.info("gh api: %s", " ".join(cmd))
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if proc.returncode == 0:
                return proc.stdout
            last_err = RuntimeError(
                f"gh api failed (exit {proc.returncode}): {proc.stderr.strip()}"
            )
            if attempt < MAX_RETRIES:
                delay = BACKOFF_BASE * (2 ** attempt)
                log.warning(
                    "gh api failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, MAX_RETRIES + 1, delay, proc.stderr.strip(),
                )
                time.sleep(delay)
            else:
                log.error("gh api failed: %s", proc.stderr.strip())
        raise last_err
