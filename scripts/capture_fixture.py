"""
Usage: python scripts/capture_fixture.py <owner/repo> <pr_number> [output_path]

Fetches a real PR from GitHub and writes a fixture JSON suitable for --fake tests.
Requires GITHUB_TOKEN in environment.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from github import Github, GithubException


def capture(repo_name: str, pr_number: int, out_path: Path) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    gh = Github(token)
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    def _file(candidates: list[str]) -> str | None:
        for name in candidates:
            try:
                c = repo.get_contents(name)
                if not isinstance(c, list):
                    return c.decoded_content.decode("utf-8", errors="replace")
            except GithubException:
                pass
        return None

    files_changed = [f.filename for f in pr.get_files()]

    recent_merged: list[str] = []
    for p in repo.get_pulls(state="closed", sort="updated", direction="desc"):
        if p.merged:
            recent_merged.append(p.title)
        if len(recent_merged) >= 10:
            break

    import urllib.request
    try:
        req = urllib.request.Request(
            pr.diff_url,
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        with urllib.request.urlopen(req) as resp:
            raw_diff = resp.read().decode("utf-8", errors="replace")
    except Exception:
        raw_diff = None

    fixture = {
        "_meta": {
            "source": f"captured from {repo_name} PR #{pr_number}",
            "captured_at": datetime.now(tz=timezone.utc).isoformat(),
        },
        "repo": repo_name,
        "pr_number": pr_number,
        "pr": {
            "number": pr.number,
            "title": pr.title,
            "body": pr.body,
            "user_login": pr.user.login,
            "created_at": pr.created_at.isoformat(),
            "updated_at": pr.updated_at.isoformat(),
            "base_ref": pr.base.ref,
            "head_ref": pr.head.ref,
            "additions": pr.additions,
            "deletions": pr.deletions,
            "changed_files": pr.changed_files,
            "labels": [label.name for label in pr.labels],
            "draft": pr.draft,
            "merged": pr.merged,
            "mergeable": pr.mergeable,
            "diff_url": pr.diff_url,
        },
        "files_changed": files_changed,
        "raw_diff": raw_diff,
        "author_prior_prs": None,
        "contributing_md": _file(["CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING"]),
        "agents_md": _file(["AGENTS.md", "AGENTS.rst", "AGENTS"]),
        "recent_merged_titles": recent_merged,
    }

    out_path.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"Wrote fixture to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    repo_arg = sys.argv[1]
    pr_arg = int(sys.argv[2])
    out_arg = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("tests/fixtures/papertriage_pr9.json")

    capture(repo_arg, pr_arg, out_arg)
