from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 1  # seconds; sleeps 1, 2, 4 on retries


def _parse_search_item(item: dict) -> dict:
    """Parse an item from the GitHub search/issues endpoint into a standard dict."""
    repo_url = item.get("repository_url", "")
    segments = repo_url.rstrip("/").split("/")
    if len(segments) < 2:
        return {}
    return {
        "org": segments[-2],
        "repo": segments[-1],
        "number": item["number"],
        "title": item["title"],
        "author": item.get("user", {}).get("login", ""),
    }


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

    def active_prs(self, integration, platform) -> list[dict]:
        """Fetch all open PRs currently requiring the user's attention."""
        base_queries = [
            "is:pr is:open assignee:@me",
            "is:pr is:open review-requested:@me",
            "is:pr is:open author:@me draft:false",
        ]
        if getattr(platform, "include_mentions", False):
            base_queries.append("is:pr is:open mentions:@me")

        results = self._search_entities(
            base_queries, integration,
            item_filter=None,
        )
        log.info("active_prs: found %d unique PRs across all queries", len(results))
        return results

    def get_issue(self, org: str, repo: str, number: int) -> dict:
        result = self._gh_api(f"repos/{org}/{repo}/issues/{number}")
        return {
            "org": org,
            "repo": repo,
            "number": number,
            "title": result.get("title", ""),
            "author": result.get("user", {}).get("login", ""),
            "state": result.get("state", "unknown"),
            "labels": [l.get("name", "") for l in result.get("labels", [])],
        }

    def get_issue_detail(self, org: str, repo: str, number: int) -> dict:
        result = self._gh_api(f"repos/{org}/{repo}/issues/{number}")
        return {
            "title": result.get("title", ""),
            "body": result.get("body", "") or "",
            "author": result.get("user", {}).get("login", ""),
            "state": result.get("state", "unknown"),
            "labels": [l.get("name", "") for l in result.get("labels", [])],
            "comment_count": result.get("comments", 0),
        }

    def active_issues(self, integration, platform) -> list[dict]:
        """Fetch all open issues currently requiring the user's attention."""
        base_queries = [
            "is:issue is:open assignee:@me",
            "is:issue is:open author:@me",
        ]
        if getattr(platform, "include_mentions", False):
            base_queries.append("is:issue is:open mentions:@me")

        results = self._search_entities(
            base_queries, integration,
            item_filter=lambda item: "pull_request" not in item,
        )
        log.info("active_issues: found %d unique issues across all queries", len(results))
        return results

    def _search_entities(
        self,
        base_queries: list[str],
        integration,
        item_filter: Callable[[dict], bool] | None = None,
    ) -> list[dict]:
        """Execute search queries and return deduplicated entity dicts.

        item_filter, when provided, is applied to each raw search result item
        before parsing (e.g., to exclude PRs from issue searches).
        """
        seen: set[tuple[str, str, int]] = set()
        results: list[dict] = []

        scopes = self._scope_qualifiers(integration)
        for base_query in base_queries:
            for scope in scopes:
                query = f"{base_query} {scope}".strip()
                for item in self._search_raw(query, item_filter):
                    key = (item["org"], item["repo"], item["number"])
                    if key not in seen:
                        seen.add(key)
                        results.append(item)
        return results

    def _search_raw(
        self,
        query: str,
        item_filter: Callable[[dict], bool] | None = None,
    ) -> list[dict]:
        """Execute a GitHub search/issues query and return parsed entity dicts."""
        result = self._gh_api(
            "search/issues",
            params={"q": query, "per_page": "100"},
        )
        entities = []
        for item in result.get("items", []):
            if item_filter is not None and not item_filter(item):
                continue
            parsed = _parse_search_item(item)
            if not parsed:
                log.warning("Cannot parse org/repo from repository_url: %s", item.get("repository_url", ""))
                continue
            entities.append(parsed)
        log.info("_search_raw(%r): found %d results", query, len(entities))
        return entities

    def _scope_qualifiers(self, integration) -> list[str]:
        """Build scope qualifiers from the integration's org/repo config."""
        qualifiers = []
        for org in (integration.orgs or []):
            qualifiers.append(f"org:{org}")
        for repo in (integration.repos or []):
            qualifiers.append(f"repo:{repo}")
        return qualifiers or [""]

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
