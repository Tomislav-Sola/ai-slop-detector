from __future__ import annotations

import json
from pathlib import Path

import pytest

from pr_triage.golden import GoldenBuildError, build_golden_set


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _write_labels(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _write_candidate(cand_dir: Path, repo: str, pr_number: int, **extra) -> None:
    safe = repo.replace("/", "__")
    path = cand_dir / f"{safe}_pr{pr_number}.json"
    data = {
        "repo": repo,
        "pr_number": pr_number,
        "title": f"PR #{pr_number}",
        "additions": 50,
        "deletions": 10,
        **extra,
    }
    path.write_text(json.dumps(data))


def _make_full_label_set(labels_path: Path, cand_dir: Path) -> None:
    """Write 30 entries (10 approve, 15 request_changes, 5 reject) to labels and candidates."""
    entries = (
        [{"repo": "o/r", "pr_number": i, "label": "approve"} for i in range(1, 11)]
        + [{"repo": "o/r", "pr_number": i, "label": "request_changes"} for i in range(11, 26)]
        + [{"repo": "o/r", "pr_number": i, "label": "reject"} for i in range(26, 31)]
    )
    _write_labels(labels_path, entries)
    for e in entries:
        _write_candidate(cand_dir, e["repo"], e["pr_number"])


# ------------------------------------------------------------------
# Error cases
# ------------------------------------------------------------------

def test_missing_labels_file_raises(tmp_path):
    with pytest.raises(GoldenBuildError, match="Labels file not found"):
        build_golden_set(tmp_path / "missing.jsonl", tmp_path / "cands", tmp_path / "out")


def test_invalid_label_value_raises(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    _write_labels(labels_path, [{"repo": "o/r", "pr_number": 1, "label": "maybe"}])
    with pytest.raises(GoldenBuildError, match="Invalid label"):
        build_golden_set(labels_path, cand_dir, tmp_path / "out")


def test_missing_label_field_raises(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    _write_labels(labels_path, [{"repo": "o/r", "pr_number": 1}])
    with pytest.raises(GoldenBuildError, match="Missing 'label'"):
        build_golden_set(labels_path, tmp_path / "cands", tmp_path / "out")


def test_too_few_total_raises(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    cand_dir.mkdir()
    entries = [{"repo": "o/r", "pr_number": i, "label": "approve"} for i in range(1, 6)]
    _write_labels(labels_path, entries)
    for e in entries:
        _write_candidate(cand_dir, "o/r", e["pr_number"])
    with pytest.raises(GoldenBuildError, match="at least 30"):
        build_golden_set(labels_path, cand_dir, tmp_path / "out")


def test_too_few_per_class_raises(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    cand_dir.mkdir()
    # 30 total but reject has 0
    entries = (
        [{"repo": "o/r", "pr_number": i, "label": "approve"} for i in range(1, 16)]
        + [{"repo": "o/r", "pr_number": i, "label": "request_changes"} for i in range(16, 31)]
    )
    _write_labels(labels_path, entries)
    for e in entries:
        _write_candidate(cand_dir, "o/r", e["pr_number"])
    with pytest.raises(GoldenBuildError, match="'reject'"):
        build_golden_set(labels_path, cand_dir, tmp_path / "out")


def test_missing_candidate_file_raises(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    cand_dir.mkdir()
    _write_labels(labels_path, [{"repo": "o/r", "pr_number": 99, "label": "approve"}])
    with pytest.raises(GoldenBuildError, match="Candidate file not found"):
        build_golden_set(labels_path, cand_dir, tmp_path / "out", force=True)


def test_invalid_json_in_labels_raises(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text("not json\n")
    with pytest.raises(GoldenBuildError, match="Invalid JSON"):
        build_golden_set(labels_path, tmp_path / "cands", tmp_path / "out")


# ------------------------------------------------------------------
# Success cases
# ------------------------------------------------------------------

def test_build_golden_writes_files(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    cand_dir.mkdir()
    out_dir = tmp_path / "out"

    _make_full_label_set(labels_path, cand_dir)
    summary = build_golden_set(labels_path, cand_dir, out_dir)

    assert summary["total"] == 30
    assert summary["approve"] == 10
    assert summary["request_changes"] == 15
    assert summary["reject"] == 5
    assert len(list(out_dir.glob("*.json"))) == 30


def test_build_golden_merges_candidate_data(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    cand_dir.mkdir()
    out_dir = tmp_path / "out"

    _make_full_label_set(labels_path, cand_dir)
    build_golden_set(labels_path, cand_dir, out_dir)

    sample = json.loads((out_dir / "o__r_pr1.json").read_text())
    assert sample["golden_label"] == "approve"
    assert sample["pr_number"] == 1
    assert "title" in sample


def test_build_golden_stores_notes(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    cand_dir.mkdir()
    out_dir = tmp_path / "out"

    _make_full_label_set(labels_path, cand_dir)
    # Overwrite one entry with a note
    entries_raw = labels_path.read_text().splitlines()
    first = json.loads(entries_raw[0])
    first["notes"] = "obvious trivial change"
    entries_raw[0] = json.dumps(first)
    labels_path.write_text("\n".join(entries_raw) + "\n")

    build_golden_set(labels_path, cand_dir, out_dir)
    sample = json.loads((out_dir / "o__r_pr1.json").read_text())
    assert sample["label_notes"] == "obvious trivial change"


def test_build_golden_force_bypasses_validation(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    cand_dir.mkdir()
    out_dir = tmp_path / "out"

    entries = [{"repo": "o/r", "pr_number": i, "label": "approve"} for i in range(1, 4)]
    _write_labels(labels_path, entries)
    for e in entries:
        _write_candidate(cand_dir, "o/r", e["pr_number"])

    summary = build_golden_set(labels_path, cand_dir, out_dir, force=True)
    assert summary["total"] == 3


def test_build_golden_skips_blank_lines(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    cand_dir = tmp_path / "cands"
    cand_dir.mkdir()
    out_dir = tmp_path / "out"

    cand_dir.mkdir(exist_ok=True)
    _write_candidate(cand_dir, "o/r", 1)
    labels_path.write_text(
        '\n{"repo": "o/r", "pr_number": 1, "label": "approve"}\n\n'
    )
    summary = build_golden_set(labels_path, cand_dir, out_dir, force=True)
    assert summary["total"] == 1
