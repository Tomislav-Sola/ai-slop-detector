from __future__ import annotations

from datetime import datetime, timezone

from github import Github, GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

from pr_triage.state import PRMetadata, TriageState

_CONTRIBUTING_CANDIDATES = ["CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING"]
_AGENTS_CANDIDATES = ["AGENTS.md", "AGENTS.rst", "AGENTS"]


def _utc(dt: datetime) -> datetime:
    """Ensure a datetime is UTC-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fetch_file(repo: Repository, candidates: list[str]) -> str | None:
    for name in candidates:
        try:
            content = repo.get_contents(name)
            if isinstance(content, list):
                continue
            return content.decoded_content.decode("utf-8", errors="replace")
        except GithubException:
            continue
    return None


class GitHubClient:
    def __init__(self, token: str) -> None:
        self._gh = Github(token)
        self._token = token

    def fetch_pr(
        self,
        repo_name: str,
        pr_number: int,
        *,
        recent_n: int = 10,
    ) -> TriageState:
        repo = self._gh.get_repo(repo_name)
        pr: PullRequest = repo.get_pull(pr_number)

        metadata = PRMetadata(
            number=pr.number,
            title=pr.title,
            body=pr.body,
            author=pr.user.login,
            created_at=_utc(pr.created_at),
            updated_at=_utc(pr.updated_at),
            base_branch=pr.base.ref,
            head_branch=pr.head.ref,
            additions=pr.additions,
            deletions=pr.deletions,
            changed_files=pr.changed_files,
            labels=[label.name for label in pr.labels],
            draft=pr.draft,
            merged=pr.merged,
            mergeable=pr.mergeable,
        )

        files_changed = [f.filename for f in pr.get_files()]

        raw_diff = _fetch_diff(pr, self._token)

        author_prior_prs = _count_author_prs(repo, pr.user.login, exclude_pr=pr_number)

        contributing_md = _fetch_file(repo, _CONTRIBUTING_CANDIDATES)
        agents_md = _fetch_file(repo, _AGENTS_CANDIDATES)

        recent_merged_titles = _recent_merged_titles(repo, n=recent_n)

        return TriageState(
            repo=repo_name,
            pr_number=pr_number,
            fetched_at=datetime.now(tz=timezone.utc),
            metadata=metadata,
            raw_diff=raw_diff,
            files_changed=files_changed,
            author_prior_prs=author_prior_prs,
            contributing_md=contributing_md,
            agents_md=agents_md,
            recent_merged_titles=recent_merged_titles,
        )


    def fetch_repo_context(
        self,
        repo_name: str,
        *,
        recent_n: int = 50,
    ) -> dict:
        """Fetch repo-level context for RAG indexing.

        Returns contributing_md, agents_md, and the last recent_n merged PR
        titles + bodies.
        """
        repo = self._gh.get_repo(repo_name)
        contributing_md = _fetch_file(repo, _CONTRIBUTING_CANDIDATES)
        agents_md = _fetch_file(repo, _AGENTS_CANDIDATES)
        merged_prs: list[dict] = []
        for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
            if pr.merged:
                merged_prs.append({"title": pr.title, "body": pr.body or ""})
            if len(merged_prs) >= recent_n:
                break
        return {
            "contributing_md": contributing_md,
            "agents_md": agents_md,
            "merged_prs": merged_prs,
        }


def _fetch_diff(pr: PullRequest, token: str | None = None) -> str | None:
    try:
        import urllib.request

        headers = {"Accept": "application/vnd.github.v3.diff"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(pr.diff_url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _count_author_prs(repo: Repository, login: str, *, exclude_pr: int) -> int:
    count = 0
    for pr in repo.get_pulls(state="all"):
        if pr.user.login == login and pr.number != exclude_pr:
            count += 1
    return count


def _recent_merged_titles(repo: Repository, *, n: int) -> list[str]:
    titles: list[str] = []
    for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
        if pr.merged:
            titles.append(pr.title)
        if len(titles) >= n:
            break
    return titles
