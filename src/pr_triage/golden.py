from __future__ import annotations

import json
from pathlib import Path

_MIN_TOTAL = 30
_MIN_PER_CLASS = 5
_VALID_LABELS = frozenset({"accepted", "rejected_quality", "slop"})
_SKIP_LABEL = "skip"


class GoldenBuildError(Exception):
    pass


def build_golden_set(
    labels_path: Path,
    candidates_dir: Path,
    out_dir: Path,
    *,
    min_total: int = _MIN_TOTAL,
    min_per_class: int = _MIN_PER_CLASS,
    force: bool = False,
) -> dict:
    """Merge manual labels with harvested candidate data into golden fixture files.

    Each entry in labels_path (JSONL) must have at minimum:
      { "repo": "owner/repo", "pr_number": 7, "label": "accepted" }

    Entries with label "skip" are silently ignored (not written, not counted).
    Matching candidate files are read from candidates_dir.
    Per-entry golden JSON files are written to out_dir.

    Returns a summary dict: { "total": N, "accepted": N, "rejected_quality": N, "slop": N }

    Raises GoldenBuildError if:
    - labels_path does not exist
    - any label is not in VALID_LABELS (and not "skip")
    - total < min_total (unless force=True)
    - any class has < min_per_class entries (unless force=True)
    - a referenced candidate file is missing
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
                continue  # silently exclude skipped entries
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

    if not force:
        if total < min_total:
            raise GoldenBuildError(
                f"Need at least {min_total} labeled entries, got {total}"
            )
        for cls, count in class_counts.items():
            if count < min_per_class:
                raise GoldenBuildError(
                    f"Need at least {min_per_class} '{cls}' entries, got {count}"
                )

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0

    for label_entry in labels:
        repo = label_entry.get("repo", "")
        pr_number = label_entry.get("pr_number")
        if not repo or pr_number is None:
            raise GoldenBuildError(
                f"Label entry missing 'repo' or 'pr_number': {label_entry}"
            )

        safe = repo.replace("/", "__")
        candidate_file = candidates_dir / f"{safe}_pr{pr_number}.json"
        if not candidate_file.exists():
            raise GoldenBuildError(
                f"Candidate file not found: {candidate_file}"
            )

        candidate = json.loads(candidate_file.read_text())
        golden_entry = {
            **candidate,
            "golden_label": label_entry["label"],
            "label_notes": label_entry.get("notes", ""),
        }

        out_file = out_dir / f"{safe}_pr{pr_number}.json"
        out_file.write_text(json.dumps(golden_entry, indent=2))
        written += 1

    return {"total": written, **class_counts}
