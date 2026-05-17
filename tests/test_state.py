from datetime import datetime, timezone

import pytest

from ai_slop_detector.state import CriticOutput, PRMetadata, TriageState, Verdict


def _make_metadata(**overrides) -> PRMetadata:
    base = dict(
        number=1,
        title="feat: add thing",
        author="alice",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        base_branch="main",
        head_branch="feat/thing",
        additions=10,
        deletions=2,
        changed_files=1,
    )
    base.update(overrides)
    return PRMetadata(**base)


def test_triage_state_defaults():
    state = TriageState(
        repo="owner/repo",
        pr_number=1,
        metadata=_make_metadata(),
    )
    assert state.critic_outputs == []
    assert state.aggregate_verdict is None
    assert state.rag_chunks == []
    assert state.confidence_score is None
    assert state.raw_diff is None
    assert state.contributing_md is None
    assert state.agents_md is None


def test_triage_state_roundtrip_json():
    state = TriageState(
        repo="owner/repo",
        pr_number=42,
        metadata=_make_metadata(number=42),
        raw_diff="diff --git a/foo.py ...",
        files_changed=["foo.py"],
        author_prior_prs=3,
    )
    dumped = state.model_dump_json()
    restored = TriageState.model_validate_json(dumped)
    assert restored.pr_number == 42
    assert restored.files_changed == ["foo.py"]
    assert restored.author_prior_prs == 3


def test_triage_state_with_critic_outputs():
    critic = CriticOutput(
        critic_name="style",
        verdict="pass",
        reasoning="Looks fine.",
        confidence=0.9,
    )
    verdict = Verdict(decision="approve", summary="LGTM", confidence=0.85)
    state = TriageState(
        repo="owner/repo",
        pr_number=1,
        metadata=_make_metadata(),
        critic_outputs=[critic],
        aggregate_verdict=verdict,
    )
    assert len(state.critic_outputs) == 1
    assert state.aggregate_verdict.decision == "approve"


def test_pr_metadata_labels_default_empty():
    meta = _make_metadata()
    assert meta.labels == []
    assert meta.draft is False
    assert meta.merged is False
