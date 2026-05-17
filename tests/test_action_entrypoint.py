"""Unit tests for the GitHub Action entry point.

End-to-end pipeline invocation is covered by the smoke-test repo (see CLAUDE.md);
these tests cover the bits that should not require live GitHub or Anthropic:
comment rendering, idempotent upsert, pagination, input parsing, and the
fail-open exit paths.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from ai_slop_detector.action_entrypoint import (
    COMMENT_MARKER,
    _bool_input,
    _build_clean_comment,
    _build_failure_comment,
    _build_slop_comment,
    _find_marker_comment,
    _int_input,
    _upsert_comment,
    main,
)
from ai_slop_detector.state import AggregateResult, PRMetadata, TriageState


def _make_state(decision: str = "reject") -> TriageState:
    return TriageState(
        repo="owner/repo",
        pr_number=42,
        metadata=PRMetadata(
            number=42,
            title="Test PR",
            author="testuser",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            base_branch="main",
            head_branch="feature",
            additions=10,
            deletions=5,
            changed_files=2,
        ),
        aggregate_result=AggregateResult(
            decision=decision,
            summary="Weighted score 3.0/10 → slop (reject).",
            per_critic_scores={"architecture_critic": 3, "slop_signals_critic": 4},
            deciding_factors=[
                "AI-generated footer detected at end of PR description",
                "Vague description with generic 'improvements' phrasing",
                "No tests added for changed code paths",
                "Fourth factor that should be trimmed",
                "Fifth factor that should be trimmed",
            ],
        ),
    )


# ------------------------------------------------------------------
# Comment rendering
# ------------------------------------------------------------------

def test_slop_comment_contains_marker_scores_and_factors() -> None:
    state = _make_state("reject")
    body = _build_slop_comment(state, state.aggregate_result)

    # Marker first so re-runs can find this comment.
    assert body.lstrip().startswith(COMMENT_MARKER)

    # Slop framing + critic names + scores.
    assert "looks like AI slop" in body
    assert "architecture_critic" in body
    assert "slop_signals_critic" in body
    assert "**3**/10" in body  # architecture
    assert "**4**/10" in body  # slop_signals

    # At most 3 deciding factors surfaced; the 4th/5th must not appear.
    assert "AI-generated footer detected at end of PR description" in body
    assert "Fourth factor that should be trimmed" not in body
    assert "Fifth factor that should be trimmed" not in body

    # Honest disclosure must mention false positives + maintainer call.
    assert "false positive" in body.lower()
    assert "Make your own call" in body

    # Link to scoring docs.
    assert "how-scoring-works" in body


def test_slop_comment_with_no_factors_shows_placeholder() -> None:
    state = _make_state("reject")
    state.aggregate_result.deciding_factors = []
    body = _build_slop_comment(state, state.aggregate_result)
    assert "no specific findings surfaced" in body


def test_clean_comment_contains_marker_and_clean_wording() -> None:
    state = _make_state("approve")
    body = _build_clean_comment(state, state.aggregate_result)
    assert body.lstrip().startswith(COMMENT_MARKER)
    assert "looks clean" in body
    assert state.aggregate_result.summary in body


def test_failure_comment_includes_exception_type() -> None:
    body = _build_failure_comment(ValueError("boom"))
    assert COMMENT_MARKER in body
    assert "couldn't analyse" in body
    assert "ValueError" in body


# ------------------------------------------------------------------
# Idempotent upsert
# ------------------------------------------------------------------

def test_upsert_posts_when_no_marker_exists() -> None:
    with patch("ai_slop_detector.action_entrypoint._find_marker_comment", return_value=None), \
         patch("ai_slop_detector.action_entrypoint._post_comment") as post, \
         patch("ai_slop_detector.action_entrypoint._patch_comment") as patch_call:
        _upsert_comment("owner/repo", 1, "tok", "body")
    post.assert_called_once_with("owner/repo", 1, "tok", "body")
    patch_call.assert_not_called()


def test_upsert_patches_when_marker_found() -> None:
    with patch("ai_slop_detector.action_entrypoint._find_marker_comment", return_value=999), \
         patch("ai_slop_detector.action_entrypoint._post_comment") as post, \
         patch("ai_slop_detector.action_entrypoint._patch_comment") as patch_call:
        _upsert_comment("owner/repo", 1, "tok", "body")
    patch_call.assert_called_once_with("owner/repo", 999, "tok", "body")
    post.assert_not_called()


def test_find_marker_paginates_until_match() -> None:
    # Page 1: 100 unrelated comments (forces a second page request).
    # Page 2: contains the marker.
    page1 = [{"id": i, "body": "noise"} for i in range(100)]
    page2 = [{"id": 5000, "body": f"prelude {COMMENT_MARKER} body"}]
    with patch("ai_slop_detector.action_entrypoint._gh_request") as gh:
        gh.side_effect = [page1, page2]
        result = _find_marker_comment("owner/repo", 1, "tok")
    assert result == 5000
    assert gh.call_count == 2


def test_find_marker_returns_none_when_no_comments() -> None:
    with patch("ai_slop_detector.action_entrypoint._gh_request", return_value=[]):
        assert _find_marker_comment("owner/repo", 1, "tok") is None


def test_find_marker_stops_on_partial_page() -> None:
    # Less than per_page=100 results = last page; no further requests needed.
    page = [{"id": 1, "body": "no marker here"}]
    with patch("ai_slop_detector.action_entrypoint._gh_request") as gh:
        gh.side_effect = [page]
        assert _find_marker_comment("owner/repo", 1, "tok") is None
    assert gh.call_count == 1


# ------------------------------------------------------------------
# Input parsing
# ------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ("", None),  # sentinel: returns default
])
def test_bool_input(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool | None) -> None:
    if value:
        monkeypatch.setenv("INPUT_FLAG", value)
    else:
        monkeypatch.delenv("INPUT_FLAG", raising=False)
    if expected is None:
        assert _bool_input("FLAG", default=True) is True
        assert _bool_input("FLAG", default=False) is False
    else:
        assert _bool_input("FLAG", default=not expected) is expected


def test_int_input_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INPUT_MAX", "12345")
    assert _int_input("MAX", default=999) == 12345


def test_int_input_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INPUT_MAX", "not-a-number")
    assert _int_input("MAX", default=777) == 777


def test_int_input_unset_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INPUT_MAX", raising=False)
    assert _int_input("MAX", default=42) == 42


# ------------------------------------------------------------------
# main() fail-open paths
# ------------------------------------------------------------------

def test_main_no_event_path_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert main() == 0


def test_main_non_pull_request_event_exits_zero(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"action": "push", "repository": {"full_name": "o/r"}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    assert main() == 0


def test_main_unparseable_event_exits_zero(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text("{not valid json")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    assert main() == 0


def test_main_missing_anthropic_key_exits_zero_without_comment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({
        "pull_request": {"number": 1},
        "repository": {"full_name": "o/r"},
    }))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.delenv("INPUT_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("ai_slop_detector.action_entrypoint._upsert_comment") as upsert:
        assert main() == 0
    # Configuration error → no posting (no token to authenticate with anyway).
    upsert.assert_not_called()


def test_main_pipeline_exception_posts_failure_comment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({
        "pull_request": {"number": 7},
        "repository": {"full_name": "o/r"},
    }))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.setenv("INPUT_ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "ghs-test")

    with patch("ai_slop_detector.action_entrypoint._run", side_effect=RuntimeError("pipeline blew up")), \
         patch("ai_slop_detector.action_entrypoint._upsert_comment") as upsert:
        assert main() == 0

    upsert.assert_called_once()
    posted_body = upsert.call_args[0][3]
    assert COMMENT_MARKER in posted_body
    assert "couldn't analyse" in posted_body
    assert "RuntimeError" in posted_body
