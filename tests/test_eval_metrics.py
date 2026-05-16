"""Unit tests for eval.py metric computation — no LLM calls needed."""
from __future__ import annotations

from pr_triage.eval import compute_metrics, load_golden_entries, entry_to_state


def _result(golden_label: str, predicted: str) -> dict:
    return {"golden_label": golden_label, "predicted_decision": predicted}


# ------------------------------------------------------------------
# compute_metrics
# ------------------------------------------------------------------

def test_perfect_accuracy():
    """Binary metric: rejected_quality maps to approve (not slop)."""
    results = [
        _result("accepted", "approve"),
        _result("rejected_quality", "approve"),
        _result("slop", "reject"),
    ]
    m = compute_metrics(results)
    assert m["accuracy"] == 1.0
    assert m["n_correct"] == 3


def test_zero_accuracy():
    results = [
        _result("accepted", "reject"),
        _result("rejected_quality", "reject"),
        _result("slop", "approve"),
    ]
    m = compute_metrics(results)
    assert m["accuracy"] == 0.0
    assert m["n_correct"] == 0


def test_partial_accuracy():
    results = [
        _result("accepted", "approve"),    # correct
        _result("slop", "approve"),         # wrong (slop predicted not-slop)
        _result("slop", "reject"),          # correct
    ]
    m = compute_metrics(results)
    assert m["n_correct"] == 2
    assert abs(m["accuracy"] - 2 / 3) < 0.001


def test_slop_precision_recall_f1_perfect():
    """All slop cases predicted reject, all non-slop predicted approve."""
    results = [
        _result("accepted", "approve"),
        _result("rejected_quality", "approve"),
        _result("slop", "reject"),
    ]
    m = compute_metrics(results)
    assert m["slop_precision"] == 1.0
    assert m["slop_recall"] == 1.0
    assert m["slop_f1"] == 1.0


def test_confusion_matrix_shape():
    """Binary confusion matrix has only approve/reject rows and cols."""
    results = [_result("accepted", "approve")]
    m = compute_metrics(results)
    cm = m["confusion_matrix"]
    for row in ["approve", "reject"]:
        assert row in cm
        for col in ["approve", "reject"]:
            assert col in cm[row]


def test_empty_results():
    m = compute_metrics([])
    assert m["accuracy"] == 0.0
    assert m["n_total"] == 0


def test_zero_division_safe():
    """A class with 0 TP, 0 FP, 0 FN should not crash."""
    results = [_result("accepted", "approve")]
    m = compute_metrics(results)
    # No slop examples present → slop precision/recall/F1 should be 0, not error.
    assert m["slop_precision"] == 0.0
    assert m["slop_recall"] == 0.0
    assert m["slop_f1"] == 0.0


def test_by_golden_class_breakdown_kept():
    """Secondary 3-class breakdown helps debugging — should remain in the output."""
    results = [
        _result("accepted", "approve"),
        _result("rejected_quality", "reject"),  # false positive
        _result("slop", "reject"),
    ]
    m = compute_metrics(results)
    by = m["by_golden_class"]
    assert by["accepted"]["approve"] == 1
    assert by["rejected_quality"]["reject"] == 1
    assert by["slop"]["reject"] == 1


# ------------------------------------------------------------------
# entry_to_state
# ------------------------------------------------------------------

def test_entry_to_state_minimal():
    """First-look mode: entry_to_state always sets merged=False (PR-open simulation),
    even when the fixture records merged=True. The aggregator must not depend on
    the eventual outcome.
    """
    entry = {
        "repo": "o/r",
        "pr_number": 42,
        "title": "Fix bug",
        "author": "alice",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "base_branch": "main",
        "head_branch": "fix-bug",
        "additions": 10,
        "deletions": 2,
        "changed_files": 1,
        "merged": True,  # fixture says merged
        "golden_label": "accepted",
        "label_notes": "",
    }
    state = entry_to_state(entry)
    assert state.repo == "o/r"
    assert state.pr_number == 42
    # First-look forces merged=False; outcome leakage is not allowed.
    assert state.metadata.merged is False


def test_entry_to_state_files_changed_dict_format():
    """files_changed may be a list of dicts from GitHub API."""
    entry = {
        "repo": "o/r",
        "pr_number": 1,
        "title": "t",
        "author": "a",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "base_branch": "main",
        "head_branch": "b",
        "additions": 1, "deletions": 0, "changed_files": 1,
        "merged": False,
        "files_changed": [{"filename": "src/foo.py"}],
        "golden_label": "slop",
        "label_notes": "",
    }
    state = entry_to_state(entry)
    assert state.files_changed == ["src/foo.py"]
