from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_MIN_TOTAL = 30
_MIN_PER_BINARY_CLASS = 5  # minimum samples on each side of is_slop True/False


class GoldenBuildError(Exception):
    pass


def _find_candidate(
    repo: str,
    pr_number: int,
    candidates_dirs: list[Path],
) -> Path:
    """Return the first matching candidate file found across all dirs."""
    safe = repo.replace("/", "__")
    fname = f"{safe}_pr{pr_number}.json"
    for d in candidates_dirs:
        p = d / fname
        if p.exists():
            return p
    searched = ", ".join(str(d) for d in candidates_dirs)
    raise GoldenBuildError(
        f"Candidate file not found for {repo}#{pr_number} "
        f"(searched: {searched})"
    )


def build_golden_set(
    labels_path: Path,
    candidates_dirs: list[Path],
    out_dir: Path,
    *,
    min_total: int = _MIN_TOTAL,
    min_per_class: int = _MIN_PER_BINARY_CLASS,
    force: bool = False,
) -> dict:
    """Merge manual labels with harvested candidate data into golden fixture files.

    Each entry in labels_path (JSONL) must have:
      { "repo": "owner/repo", "pr_number": 7, "is_slop": bool }

    Entries with `"skip": true` are silently ignored (not written, not counted).
    Candidate files are searched across all candidates_dirs in order.
    Per-entry golden JSON files are written to out_dir.
    A manifest.json is written to out_dir with the binary distribution and repo counts.

    Returns a summary dict: { "total": N, "is_slop": N_slop, "not_slop": N_not_slop }

    Raises GoldenBuildError if:
    - labels_path does not exist
    - any entry is missing the `is_slop` field (and is not a skip)
    - total < min_total (unless force=True)
    - either side of the binary split has < min_per_class entries (unless force=True)
    - a referenced candidate file is missing in all candidates_dirs
    """
    if not labels_path.exists():
        raise GoldenBuildError(f"Labels file not found: {labels_path}")

    labels: list[dict] = []
    with labels_path.open() as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GoldenBuildError(f"Invalid JSON on line {lineno}: {exc}") from exc
            if entry.get("skip"):
                continue
            if "is_slop" not in entry:
                raise GoldenBuildError(
                    f"Missing 'is_slop' field on line {lineno}: {entry}"
                )
            if not isinstance(entry["is_slop"], bool):
                raise GoldenBuildError(
                    f"'is_slop' must be a bool on line {lineno}, got {type(entry['is_slop']).__name__}"
                )
            labels.append(entry)

    n_slop = sum(1 for e in labels if e["is_slop"])
    n_not_slop = sum(1 for e in labels if not e["is_slop"])
    total = n_slop + n_not_slop

    if not force:
        if total < min_total:
            raise GoldenBuildError(
                f"Need at least {min_total} labeled entries, got {total}"
            )
        if n_slop < min_per_class:
            raise GoldenBuildError(
                f"Need at least {min_per_class} slop entries, got {n_slop}"
            )
        if n_not_slop < min_per_class:
            raise GoldenBuildError(
                f"Need at least {min_per_class} not-slop entries, got {n_not_slop}"
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    repo_counts: Counter = Counter()
    written = 0

    for label_entry in labels:
        repo = label_entry.get("repo", "")
        pr_number = label_entry.get("pr_number")
        if not repo or pr_number is None:
            raise GoldenBuildError(
                f"Label entry missing 'repo' or 'pr_number': {label_entry}"
            )

        candidate_file = _find_candidate(repo, pr_number, candidates_dirs)
        candidate = json.loads(candidate_file.read_text())
        golden_entry = {
            **candidate,
            "is_slop": label_entry["is_slop"],
        }

        safe = repo.replace("/", "__")
        out_file = out_dir / f"{safe}_pr{pr_number}.json"
        out_file.write_text(json.dumps(golden_entry, indent=2))
        repo_counts[repo] += 1
        written += 1

    manifest = {
        "total": written,
        "is_slop_counts": {"slop": n_slop, "not_slop": n_not_slop},
        "repo_counts": dict(repo_counts.most_common()),
        "candidates_dirs": [str(d) for d in candidates_dirs],
        "built_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return {"total": written, "is_slop": n_slop, "not_slop": n_not_slop}
