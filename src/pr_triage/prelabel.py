from __future__ import annotations

import json
from pathlib import Path

from pr_triage.graph.nodes import _DOC_AND_CONFIG_SUFFIXES

_TRIVIAL_LINE_THRESHOLD = 10
_REJECT_LABELS = frozenset({"invalid", "wontfix", "duplicate", "spam"})
_REQUEST_CHANGES_LABELS = frozenset(
    {"needs-work", "needs-revision", "changes-requested", "ci-failed", "blocked", "wip"}
)
_DOC_BASENAMES = frozenset(
    {"license", "licence", "makefile", "dockerfile", "codeowners", "notice", "authors"}
)


def prelabel_candidate(candidate: dict) -> str:
    """Assign a heuristic pre-label to a single PR candidate dict.

    Returns one of: "approve", "request_changes", "reject".

    Heuristics (in priority order):
    1. Reject: PR has a reject-signal label (invalid, wontfix, duplicate, spam)
    2. Approve: tiny changeset (additions + deletions < 10)
    3. Approve: all changed files are docs/config (no executable code)
    4. Request changes: draft PR
    5. Request changes: PR has a request-changes-signal label
    6. Default: request_changes
    """
    additions = candidate.get("additions", 0)
    deletions = candidate.get("deletions", 0)
    files_changed: list[str] = candidate.get("files_changed", [])
    labels = {lbl.lower() for lbl in candidate.get("labels", [])}
    draft = candidate.get("draft", False)

    if labels & _REJECT_LABELS:
        return "reject"

    total = additions + deletions
    if total < _TRIVIAL_LINE_THRESHOLD:
        return "approve"

    if files_changed and all(_is_non_code(f) for f in files_changed):
        return "approve"

    if draft:
        return "request_changes"

    if labels & _REQUEST_CHANGES_LABELS:
        return "request_changes"

    return "request_changes"


def _is_non_code(filename: str) -> bool:
    p = Path(filename)
    return (
        p.suffix.lower() in _DOC_AND_CONFIG_SUFFIXES
        or p.name.lower() in _DOC_BASENAMES
    )


def prelabel_dir(
    candidates_dir: Path,
    out_path: Path,
    *,
    verbose: bool = False,
) -> int:
    """Pre-label all candidate JSON files in candidates_dir and write JSONL to out_path.

    Returns the number of entries written.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with out_path.open("w") as fout:
        for candidate_file in sorted(candidates_dir.glob("*.json")):
            try:
                candidate = json.loads(candidate_file.read_text())
            except Exception:
                continue
            # Skip _meta-only files or unexpected shapes
            if "pr_number" not in candidate:
                continue

            label = prelabel_candidate(candidate)
            entry = {
                "file": candidate_file.name,
                "repo": candidate.get("repo", ""),
                "pr_number": candidate.get("pr_number"),
                "title": candidate.get("title", ""),
                "additions": candidate.get("additions", 0),
                "deletions": candidate.get("deletions", 0),
                "changed_files_count": candidate.get("changed_files", 0),
                "pre_label": label,
            }
            fout.write(json.dumps(entry) + "\n")
            if verbose:
                print(f"  {candidate_file.name}: {label}")
            count += 1

    return count
