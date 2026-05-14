from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from github import Github, GithubException
from pydantic import BaseModel, Field

# Keyword-linked references: "Closes #42", "Fixes owner/other#7", etc.
_KEYWORD_RE = re.compile(
    r"(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:es|ed)?)\s+"
    r"(?:([\w.-]+/[\w.-]+)#(\d+)|#(\d+))",
    re.IGNORECASE,
)
# Bare #NNN references (not preceded by a word char to avoid matching issue IDs in URLs)
_BARE_RE = re.compile(r"(?<!\w)#(\d+)")


class LinkedIssue(BaseModel):
    number: int
    repo: Optional[str] = None  # None = same repo; "owner/repo" for cross-repo refs
    title: Optional[str] = None  # fetched for same-repo issues only


class PRCandidate(BaseModel):
    repo: str
    pr_number: int
    title: str
    body: Optional[str] = None
    author: str
    created_at: datetime
    updated_at: datetime
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
    linked_issues: list[LinkedIssue] = Field(default_factory=list)
    raw_diff: Optional[str] = None


def parse_linked_issues(title: Optional[str], body: Optional[str]) -> list[dict]:
    """Extract linked issue references from PR title and body.

    Returns a list of dicts with keys: number (int), repo (str|None).
    Cross-repo references are stored with repo set; same-repo have repo=None.
    Results are deduplicated by (repo, number) key.
    """
    text = " ".join(filter(None, [title, body]))
    issues: list[dict] = []
    seen: set = set()

    for m in _KEYWORD_RE.finditer(text):
        if m.group(1) and m.group(2):  # cross-repo "owner/repo#NNN"
            key = (m.group(1), int(m.group(2)))
            if key not in seen:
                issues.append({"number": int(m.group(2)), "repo": m.group(1)})
                seen.add(key)
        elif m.group(3):  # same-repo #NNN
            key = (None, int(m.group(3)))
            if key not in seen:
                issues.append({"number": int(m.group(3)), "repo": None})
                seen.add(key)

    for m in _BARE_RE.finditer(text):
        key = (None, int(m.group(1)))
        if key not in seen:
            issues.append({"number": int(m.group(1)), "repo": None})
            seen.add(key)

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
    re_record: bool = False,
    verbose: bool = False,
) -> tuple[int, int]:
    """Harvest PR candidates from a GitHub repo into per-entry JSON files.

    Idempotent: existing files are skipped unless re_record=True.
    Returns (new_count, skipped_count).
    """
    if states is None:
        states = ["open", "closed"]

    out_dir.mkdir(parents=True, exist_ok=True)
    gh = Github(token)
    repo = gh.get_repo(repo_name)

    new_count = 0
    skipped_count = 0
    total_seen = 0

    for state in states:
        if total_seen >= max_prs:
            break
        for pr in repo.get_pulls(state=state, sort="updated", direction="desc"):
            if total_seen >= max_prs:
                break

            dest = candidate_path(out_dir, repo_name, pr.number)
            if dest.exists() and not re_record:
                skipped_count += 1
                total_seen += 1
                continue

            try:
                files_changed = [f.filename for f in pr.get_files()]
                raw_diff = _fetch_diff(pr, token)
                linked_raw = parse_linked_issues(pr.title, pr.body)

                linked_issues: list[LinkedIssue] = []
                for ref in linked_raw:
                    title = None
                    if ref["repo"] is None:
                        try:
                            issue = repo.get_issue(ref["number"])
                            title = issue.title
                        except GithubException:
                            pass
                    linked_issues.append(
                        LinkedIssue(number=ref["number"], repo=ref["repo"], title=title)
                    )

                def _ensure_utc(dt: datetime) -> datetime:
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

                merged_at = None
                try:
                    if pr.merged and pr.merged_at:
                        merged_at = _ensure_utc(pr.merged_at)
                except Exception:
                    pass

                candidate = PRCandidate(
                    repo=repo_name,
                    pr_number=pr.number,
                    title=pr.title,
                    body=pr.body,
                    author=pr.user.login,
                    created_at=_ensure_utc(pr.created_at),
                    updated_at=_ensure_utc(pr.updated_at),
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
                    linked_issues=linked_issues,
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

            total_seen += 1

    return new_count, skipped_count


def estimate_harvest_calls(
    repo_name: str,
    token: str,
    out_dir: Path,
    *,
    states: list[str] | None = None,
    max_prs: int = 200,
    re_record: bool = False,
) -> dict:
    """Estimate how many new PRs will be fetched without actually fetching them.

    Returns a dict with keys: estimated_new, already_cached, total_open, total_closed.
    """
    if states is None:
        states = ["open", "closed"]

    gh = Github(token)
    repo = gh.get_repo(repo_name)

    total: dict[str, int] = {}
    already_cached = 0
    estimated_new = 0
    total_seen = 0

    for state in states:
        count = 0
        for pr in repo.get_pulls(state=state, sort="updated", direction="desc"):
            if total_seen >= max_prs:
                break
            dest = candidate_path(out_dir, repo_name, pr.number)
            if dest.exists() and not re_record:
                already_cached += 1
            else:
                estimated_new += 1
            count += 1
            total_seen += 1
        total[f"total_{state}"] = count

    return {"estimated_new": estimated_new, "already_cached": already_cached, **total}


def _fetch_diff(pr, token: str | None = None) -> Optional[str]:
    try:
        headers = {"Accept": "application/vnd.github.v3.diff"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(pr.diff_url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
