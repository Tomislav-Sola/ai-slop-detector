from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Optional

from github import Github, GithubException
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Regex patterns for linked-issue detection
# --------------------------------------------------------------------------

# Keyword-linked shortform: "Closes #42", "Fixes owner/other#7"
_KEYWORD_RE = re.compile(
    r"(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:es|ed)?)\s+"
    r"(?:([\w.-]+/[\w.-]+)#(\d+)|#(\d+))",
    re.IGNORECASE,
)
# Full GitHub URL: https://github.com/owner/repo/issues/1234
_GITHUB_URL_RE = re.compile(
    r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)"
)
# Bare #NNN (not preceded by a word char to avoid matching IDs in URLs)
_BARE_RE = re.compile(r"(?<!\w)#(\d+)")

_MAX_COMMENTS = 100  # cap per PR to bound file size and API calls


class LinkedIssue(BaseModel):
    number: int
    repo: Optional[str] = None  # None = same repo; "owner/repo" for cross-repo refs
    title: Optional[str] = None  # fetched for same-repo issues; None for cross-repo


class PRCandidate(BaseModel):
    repo: str
    pr_number: int
    title: str
    body: Optional[str] = None
    author: str
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    merged_at: Optional[datetime] = None
    base_branch: str
    head_branch: str
    additions: int
    deletions: int
    changed_files: int
    files_changed: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    draft: bool = False
    merged: bool = False
    # None = API error / unknown; int = actual count (may be a GitHub estimate for >1000)
    author_prior_prs_in_repo: Optional[int] = None
    linked_issues: list[LinkedIssue] = Field(default_factory=list)
    # Human-authored PR discussion thread
    issue_comments: list[dict] = Field(default_factory=list)
    # CI bots and automation comments (separated to reduce noise for critics)
    bot_comments: list[dict] = Field(default_factory=list)
    # Inline code review comments
    review_comments: list[dict] = Field(default_factory=list)
    raw_diff: Optional[str] = None


def parse_linked_issues(
    title: Optional[str],
    body: Optional[str],
    *,
    repo_name: Optional[str] = None,
) -> list[dict]:
    """Extract linked issue references from PR title and body.

    Recognises:
    - Keyword shortforms: "Closes #42", "Fixes owner/other#99"
    - Full GitHub URLs: "Closes https://github.com/owner/repo/issues/1234"
    - Bare references: "#42"

    Returns dicts with keys: number (int), repo (str|None).
    Same-repo refs have repo=None; cross-repo refs have repo="owner/repo".
    Deduplicates by (repo, number). Cross-repo refs are stored without fetching
    their body — the linked_issues entry will have title=None.
    """
    text = " ".join(filter(None, [title, body]))
    issues: list[dict] = []
    seen: set = set()

    def _add(number: int, ref_repo: Optional[str]) -> None:
        key = (ref_repo, number)
        if key not in seen:
            issues.append({"number": number, "repo": ref_repo})
            seen.add(key)

    # 1. Keyword shortforms
    for m in _KEYWORD_RE.finditer(text):
        if m.group(1) and m.group(2):  # cross-repo "owner/repo#NNN"
            _add(int(m.group(2)), m.group(1))
        elif m.group(3):  # same-repo #NNN
            _add(int(m.group(3)), None)

    # 2. Full GitHub issue URLs  e.g. https://github.com/astral-sh/ty/issues/1950
    for m in _GITHUB_URL_RE.finditer(text):
        ref_repo = m.group(1)
        num = int(m.group(2))
        # Treat as same-repo if repo_name matches, otherwise cross-repo
        normalized = None if (repo_name and ref_repo == repo_name) else ref_repo
        _add(num, normalized)

    # 3. Bare #NNN refs not already captured
    for m in _BARE_RE.finditer(text):
        _add(int(m.group(1)), None)

    return issues


def candidate_path(out_dir: Path, repo: str, pr_number: int) -> Path:
    safe = repo.replace("/", "__")
    return out_dir / f"{safe}_pr{pr_number}.json"


def harvest_repo(
    repo_name: str,
    token: str,
    out_dir: Path,
    *,
    states: list[str] | None = None,
    max_prs: int = 200,
    min_age_days: int = 14,
    re_record: bool = False,
    verbose: bool = False,
) -> tuple[int, int]:
    """Harvest closed PR candidates from a GitHub repo into per-entry JSON files.

    max_prs is the target number of NEW candidates to save — settle-filtered and
    already-cached PRs do not count toward it. The harvester keeps scanning until
    max_prs new files are written or the PR list is exhausted.

    Idempotent: existing files are skipped unless re_record=True.
    PRs closed within min_age_days of now are skipped (settle-time buffer).
    Returns (new_count, skipped_count).
    """
    if states is None:
        states = ["closed"]

    out_dir.mkdir(parents=True, exist_ok=True)
    gh = Github(token)
    repo = gh.get_repo(repo_name)

    new_count = 0
    skipped_count = 0
    author_cache: dict[str, Optional[int]] = {}

    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(days=min_age_days)
        if min_age_days > 0
        else None
    )

    for state in states:
        if new_count >= max_prs:
            break
        for pr in repo.get_pulls(state=state, sort="updated", direction="desc"):
            if new_count >= max_prs:
                break

            dest = candidate_path(out_dir, repo_name, pr.number)
            if dest.exists() and not re_record:
                skipped_count += 1
                continue

            # Settle-time filter: skip PRs closed too recently (doesn't count toward cap)
            if cutoff is not None:
                raw_closed = pr.raw_data.get("closed_at")
                if raw_closed:
                    closed_dt = datetime.fromisoformat(raw_closed.replace("Z", "+00:00"))
                    if closed_dt > cutoff:
                        age_days = (datetime.now(tz=timezone.utc) - closed_dt).days
                        if verbose:
                            print(
                                f"  skipping PR #{pr.number}: "
                                f"closed {age_days}d ago (< {min_age_days}d settle-time)"
                            )
                        skipped_count += 1
                        continue

            try:
                files_changed = [f.filename for f in pr.get_files()]
                raw_diff = _fetch_diff(pr, token)
                linked_raw = parse_linked_issues(pr.title, pr.body, repo_name=repo_name)

                linked_issues: list[LinkedIssue] = []
                for ref in linked_raw:
                    title = None
                    if ref["repo"] is None:
                        # Same-repo: fetch the issue title
                        try:
                            issue = repo.get_issue(ref["number"])
                            title = issue.title
                        except GithubException:
                            pass
                    # Cross-repo: store ref without fetching body
                    linked_issues.append(
                        LinkedIssue(number=ref["number"], repo=ref["repo"], title=title)
                    )

                human_ic, bot_ic = _fetch_issue_comments(pr)
                review_comments = _fetch_review_comments(pr)
                prior_prs = _count_author_prior_prs(gh, repo_name, pr.user.login, author_cache)

                closed_at = _safe_utc(pr.closed_at)
                merged_at = _safe_utc(pr.merged_at) if pr.merged else None

                candidate = PRCandidate(
                    repo=repo_name,
                    pr_number=pr.number,
                    title=pr.title,
                    body=pr.body,
                    author=pr.user.login,
                    created_at=_ensure_utc(pr.created_at),
                    updated_at=_ensure_utc(pr.updated_at),
                    closed_at=closed_at,
                    merged_at=merged_at,
                    base_branch=pr.base.ref,
                    head_branch=pr.head.ref,
                    additions=pr.additions,
                    deletions=pr.deletions,
                    changed_files=pr.changed_files,
                    files_changed=files_changed,
                    labels=[lbl.name for lbl in pr.labels],
                    draft=pr.draft,
                    merged=pr.merged,
                    author_prior_prs_in_repo=prior_prs,
                    linked_issues=linked_issues,
                    issue_comments=human_ic,
                    bot_comments=bot_ic,
                    review_comments=review_comments,
                    raw_diff=raw_diff,
                )

                payload = {
                    "_meta": {"harvested_at": datetime.now(tz=timezone.utc).isoformat()},
                    **json.loads(candidate.model_dump_json()),
                }
                dest.write_text(json.dumps(payload, indent=2))
                new_count += 1
                if verbose:
                    print(f"  saved {dest.name}")

            except Exception as exc:
                if verbose:
                    print(f"  skipped PR #{pr.number}: {exc}")
                skipped_count += 1

    return new_count, skipped_count


def estimate_harvest_calls(
    repo_name: str,
    token: str,
    out_dir: Path,
    *,
    states: list[str] | None = None,
    max_prs: int = 200,
    min_age_days: int = 14,
    re_record: bool = False,
) -> dict:
    """Estimate how many new PRs will be fetched without fetching any diffs.

    Returns a dict with estimated_new, already_cached, and per-state totals.
    """
    if states is None:
        states = ["closed"]

    gh = Github(token)
    repo = gh.get_repo(repo_name)

    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(days=min_age_days)
        if min_age_days > 0
        else None
    )

    totals: dict[str, int] = {}
    already_cached = 0
    estimated_new = 0

    for state in states:
        if estimated_new >= max_prs:
            break
        count = 0
        for pr in repo.get_pulls(state=state, sort="updated", direction="desc"):
            if estimated_new >= max_prs:
                break
            count += 1
            if cutoff is not None:
                raw_closed = pr.raw_data.get("closed_at")
                if raw_closed:
                    closed_dt = datetime.fromisoformat(raw_closed.replace("Z", "+00:00"))
                    if closed_dt > cutoff:
                        continue  # would be settle-filtered; doesn't count
            dest = candidate_path(out_dir, repo_name, pr.number)
            if dest.exists() and not re_record:
                already_cached += 1
            else:
                estimated_new += 1
        totals[f"total_{state}"] = count

    return {"estimated_new": estimated_new, "already_cached": already_cached, **totals}


# --------------------------------------------------------------------------
# Private helpers
# --------------------------------------------------------------------------

def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _safe_utc(dt) -> Optional[datetime]:
    try:
        if dt is None:
            return None
        return _ensure_utc(dt)
    except Exception:
        return None


def _is_bot(user: Optional[str]) -> bool:
    return bool(user and user.endswith("[bot]"))


def _comment_dict(c, *, extra: Optional[dict] = None) -> dict:
    d = {
        "user": c.user.login if c.user else None,
        "author_association": getattr(c, "author_association", None),
        "body": c.body,
        "created_at": _ensure_utc(c.created_at).isoformat() if c.created_at else None,
    }
    if extra:
        d.update(extra)
    return d


def _fetch_issue_comments(pr) -> tuple[list[dict], list[dict]]:
    """Fetch PR discussion-thread comments, split into (human, bot) lists."""
    human: list[dict] = []
    bots: list[dict] = []
    try:
        for c in islice(pr.get_issue_comments(), _MAX_COMMENTS):
            user = c.user.login if c.user else None
            target = bots if _is_bot(user) else human
            target.append(_comment_dict(c))
    except Exception:
        pass
    return human, bots


def _fetch_review_comments(pr) -> list[dict]:
    """Fetch inline code review comments (bots rarely post these; no split needed)."""
    result: list[dict] = []
    try:
        for c in islice(pr.get_review_comments(), _MAX_COMMENTS):
            result.append(_comment_dict(c, extra={"path": c.path}))
    except Exception:
        pass
    return result


def _count_author_prior_prs(
    gh: Github,
    repo_name: str,
    login: str,
    cache: dict[str, Optional[int]],
) -> Optional[int]:
    """Count closed/merged PRs by this author in this repo, excluding the current one.

    Uses the GitHub search API (one call per unique author per run, cached).
    Returns None on any API error so the caller can distinguish unknown from zero.
    Values near 1000 may be GitHub search estimates for very prolific contributors.
    """
    if login not in cache:
        try:
            results = gh.search_issues(
                query=f"is:pr is:closed repo:{repo_name} author:{login}"
            )
            cache[login] = results.totalCount
        except Exception:
            cache[login] = None

    total = cache[login]
    if total is None:
        return None
    # Subtract 1 to exclude the current PR (which is itself closed)
    return max(0, total - 1)


def _fetch_diff(pr, token: Optional[str] = None) -> Optional[str]:
    try:
        headers = {"Accept": "application/vnd.github.v3.diff"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(pr.diff_url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
