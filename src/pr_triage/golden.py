from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_MIN_TOTAL = 30
_MIN_PER_BINARY_CLASS = 5  # minimum samples on each side of is_slop True/False
_VALID_LABELS = frozenset({"accepted", "rejected_quality", "slop"})
_SKIP_LABEL = "skip"


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

    Each entry in labels_path (JSONL) must have at minimum:
      { "repo": "owner/repo", "pr_number": 7, "label": "accepted" }

    Entries with label "skip" are silently ignored (not written, not counted).
    Candidate files are searched across all candidates_dirs in order.
    Per-entry golden JSON files are written to out_dir.
    A manifest.json is written to out_dir with class and repo counts.

    Returns a summary dict: { "total": N, "accepted": N, "rejected_quality": N, "slop": N }

    Raises GoldenBuildError if:
    - labels_path does not exist
    - any label is not in VALID_LABELS (and not "skip")
    - total < min_total (unless force=True)
    - any class has < min_per_class entries (unless force=True)
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
            if "label" not in entry:
                raise GoldenBuildError(f"Missing 'label' field on line {lineno}: {entry}")
            if entry["label"] == _SKIP_LABEL:
                continue
            if entry["label"] not in _VALID_LABELS:
                raise GoldenBuildError(
                    f"Invalid label '{entry['label']}' on line {lineno} "
                    f"(must be one of {sorted(_VALID_LABELS)})"
                )
            labels.append(entry)

    class_counts: dict[str, int] = {"accepted": 0, "rejected_quality": 0, "slop": 0}
    for entry in labels:
        class_counts[entry["label"]] += 1

    total = sum(class_counts.values())
    # Binary distribution: slop is the positive class; accepted+rejected_quality fold to not-slop.
    n_slop = class_counts["slop"]
    n_not_slop = class_counts["accepted"] + class_counts["rejected_quality"]

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
            "golden_label": label_entry["label"],          # legacy 3-class, kept for analysis
            "is_slop": label_entry["label"] == "slop",     # binary primary
            "label_notes": label_entry.get("notes", ""),
        }

        safe = repo.replace("/", "__")
        out_file = out_dir / f"{safe}_pr{pr_number}.json"
        out_file.write_text(json.dumps(golden_entry, indent=2))
        repo_counts[repo] += 1
        written += 1

    manifest = {
        "total": written,
        "class_counts": class_counts,                  # legacy 3-class breakdown
        "binary_counts": {"slop": n_slop, "not_slop": n_not_slop},
        "repo_counts": dict(repo_counts.most_common()),
        "candidates_dirs": [str(d) for d in candidates_dirs],
        "built_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return {"total": written, "is_slop": n_slop, "not_slop": n_not_slop, **class_counts}
