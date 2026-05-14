from __future__ import annotations

import json
from pathlib import Path

from pr_triage.prelabel import prelabel_candidate, prelabel_dir


def _cand(**overrides) -> dict:
    base = {
        "repo": "owner/repo",
        "pr_number": 1,
        "title": "feat: something",
        "additions": 50,
        "deletions": 20,
        "changed_files": 3,
        "files_changed": ["src/main.py", "src/utils.py", "tests/test_main.py"],
        "labels": [],
        "draft": False,
        "merged": False,
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# Approve heuristics
# ------------------------------------------------------------------

def test_approve_trivial_by_line_count():
    c = _cand(additions=4, deletions=3)
    assert prelabel_candidate(c) == "approve"


def test_approve_trivial_exact_threshold():
    # total = 9 (< 10) → approve
    c = _cand(additions=5, deletions=4)
    assert prelabel_candidate(c) == "approve"


def test_approve_docs_only():
    c = _cand(
        additions=80, deletions=10,
        files_changed=["README.md", "CHANGELOG.md", "LICENSE"],
    )
    assert prelabel_candidate(c) == "approve"


def test_approve_mixed_non_code():
    c = _cand(
        additions=50, deletions=10,
        files_changed=["docs/img.png", "pyproject.toml", "README.md"],
    )
    assert prelabel_candidate(c) == "approve"


# ------------------------------------------------------------------
# Reject heuristics
# ------------------------------------------------------------------

def test_reject_invalid_label():
    c = _cand(labels=["invalid"])
    assert prelabel_candidate(c) == "reject"


def test_reject_wontfix_label():
    c = _cand(labels=["wontfix"])
    assert prelabel_candidate(c) == "reject"


def test_reject_duplicate_label():
    c = _cand(labels=["duplicate"])
    assert prelabel_candidate(c) == "reject"


def test_reject_takes_priority_over_trivial():
    # Even if very small, a "spam" label means reject
    c = _cand(additions=1, deletions=1, labels=["spam"])
    assert prelabel_candidate(c) == "reject"


# ------------------------------------------------------------------
# Request changes heuristics
# ------------------------------------------------------------------

def test_request_changes_draft():
    c = _cand(draft=True)
    assert prelabel_candidate(c) == "request_changes"


def test_request_changes_ci_failed_label():
    c = _cand(labels=["ci-failed"])
    assert prelabel_candidate(c) == "request_changes"


def test_request_changes_needs_work_label():
    c = _cand(labels=["needs-work"])
    assert prelabel_candidate(c) == "request_changes"


def test_request_changes_default_for_normal_pr():
    c = _cand()
    assert prelabel_candidate(c) == "request_changes"


# ------------------------------------------------------------------
# prelabel_dir
# ------------------------------------------------------------------

def test_prelabel_dir_writes_jsonl(tmp_path):
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir()
    out_path = tmp_path / "pre_labels.jsonl"

    for i, (adds, labels) in enumerate([
        (4, []),        # → approve (trivial)
        (200, []),      # → request_changes (default)
        (50, ["invalid"]),  # → reject
    ]):
        data = {
            "_meta": {},
            "repo": "owner/repo",
            "pr_number": i + 1,
            "title": f"PR {i + 1}",
            "additions": adds,
            "deletions": 3,
            "changed_files": 2,
            "files_changed": ["src/a.py"],
            "labels": labels,
            "draft": False,
            "merged": False,
        }
        (cand_dir / f"owner__repo_pr{i + 1}.json").write_text(json.dumps(data))

    count = prelabel_dir(cand_dir, out_path)
    assert count == 3

    lines = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 3
    pre_labels = [l["pre_label"] for l in lines]
    assert "approve" in pre_labels
    assert "request_changes" in pre_labels
    assert "reject" in pre_labels


def test_prelabel_dir_skips_invalid_json(tmp_path):
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir()
    out_path = tmp_path / "pre_labels.jsonl"

    (cand_dir / "bad.json").write_text("not json at all {{{")
    valid = {
        "repo": "owner/repo", "pr_number": 1, "title": "t",
        "additions": 5, "deletions": 0, "changed_files": 1,
        "files_changed": ["src/x.py"], "labels": [], "draft": False, "merged": False,
    }
    (cand_dir / "owner__repo_pr1.json").write_text(json.dumps(valid))

    count = prelabel_dir(cand_dir, out_path)
    assert count == 1  # bad.json skipped, valid entry written


def test_prelabel_dir_creates_output_parent(tmp_path):
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir()
    out_path = tmp_path / "nested" / "deep" / "pre_labels.jsonl"

    prelabel_dir(cand_dir, out_path)
    assert out_path.exists()
