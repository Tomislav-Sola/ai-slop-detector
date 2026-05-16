"""Unit tests for eval.py metric computation — no LLM calls needed."""
from __future__ import annotations

from pr_triage.eval import compute_metrics, load_golden_entries, entry_to_state


def _result(is_slop: bool, predicted: str) -> dict:
    return {"is_slop": is_slop, "predicted_decision": predicted}


# ------------------------------------------------------------------
# compute_metrics
# ------------------------------------------------------------------

def test_perfect_accuracy():
    results = [
        _result(False, "approve"),
        _result(False, "approve"),
        _result(True, "reject"),
    ]
    m = compute_metrics(results)
    assert m["accuracy"] == 1.0
    assert m["n_correct"] == 3


def test_zero_accuracy():
    results = [
        _result(False, "reject"),
        _result(False, "reject"),
        _result(True, "approve"),
    ]
    m = compute_metrics(results)
    assert m["accuracy"] == 0.0
    assert m["n_correct"] == 0


def test_partial_accuracy():
    results = [
        _result(False, "approve"),  # correct
        _result(True, "approve"),    # wrong (slop predicted not-slop)
        _result(True, "reject"),     # correct
    ]
    m = compute_metrics(results)
    assert m["n_correct"] == 2
    assert abs(m["accuracy"] - 2 / 3) < 0.001


def test_slop_precision_recall_f1_perfect():
    """All slop cases predicted reject, all non-slop predicted approve."""
    results = [
        _result(False, "approve"),
        _result(False, "approve"),
        _result(True, "reject"),
    ]
    m = compute_metrics(results)
    assert m["slop_precision"] == 1.0
    assert m["slop_recall"] == 1.0
    assert m["slop_f1"] == 1.0


def test_confusion_matrix_shape():
    """Binary confusion matrix has only approve/reject rows and cols."""
    results = [_result(False, "approve")]
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
    """No slop examples present — slop precision/recall/F1 should be 0, not error."""
    results = [_result(False, "approve")]
    m = compute_metrics(results)
    assert m["slop_precision"] == 0.0
    assert m["slop_recall"] == 0.0
    assert m["slop_f1"] == 0.0


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
        "is_slop": False,
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
        "is_slop": True,
    }
    state = entry_to_state(entry)
    assert state.files_changed == ["src/foo.py"]
