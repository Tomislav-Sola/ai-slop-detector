from __future__ import annotations

import pytest

from ai_slop_detector.aggregator import aggregate, _VETO_CAP, _VETO_THRESHOLD
from ai_slop_detector.state import CriticOutput, GuidelinesCriticOutput, GuidelinesFinding


def _critic(name: str, score: int) -> CriticOutput:
    details = GuidelinesCriticOutput(score=score, findings=[], citations=[])
    verdict = "pass" if score >= 8 else "needs_review" if score >= 5 else "fail"
    return CriticOutput(
        critic_name=name,
        verdict=verdict,
        reasoning=f"score {score}",
        confidence=score / 10.0,
        details=details,
    )


# ------------------------------------------------------------------
# Basic weighted average
# ------------------------------------------------------------------

def test_all_high_scores_approve():
    critics = [
        _critic("guidelines_critic", 9),
        _critic("architecture_critic", 8),
        _critic("slop_signals_critic", 9),
    ]
    result = aggregate(critics)
    assert result.decision == "approve"


def test_all_low_scores_reject():
    critics = [
        _critic("guidelines_critic", 2),
        _critic("architecture_critic", 3),
        _critic("slop_signals_critic", 1),
    ]
    result = aggregate(critics)
    assert result.decision == "reject"


def test_mid_scores_approve():
    """Binary classifier: mid-scoring PRs are not slop → approve (maintainers review)."""
    critics = [
        _critic("architecture_critic", 5),
        _critic("slop_signals_critic", 6),
    ]
    result = aggregate(critics)
    assert result.decision == "approve"


# ------------------------------------------------------------------
# Veto rule
# ------------------------------------------------------------------

def test_veto_caps_high_aggregate():
    """One critical-slop critic should veto an otherwise passing PR."""
    critics = [
        _critic("guidelines_critic", 9),
        _critic("architecture_critic", 9),
        _critic("slop_signals_critic", 1),  # veto trigger
    ]
    result = aggregate(critics)
    assert result.decision != "approve", "veto should prevent approval"
    assert "Veto applied" in result.deciding_factors[0]


def test_veto_threshold_boundary():
    """Score exactly at veto threshold triggers veto; one above does not."""
    at_threshold = [
        _critic("guidelines_critic", 9),
        _critic("architecture_critic", 9),
        _critic("slop_signals_critic", _VETO_THRESHOLD),  # exactly at threshold
    ]
    result_at = aggregate(at_threshold)
    assert result_at.decision != "approve"

    above_threshold = [
        _critic("guidelines_critic", 9),
        _critic("architecture_critic", 9),
        _critic("slop_signals_critic", _VETO_THRESHOLD + 1),
    ]
    result_above = aggregate(above_threshold)
    assert result_above.decision == "approve"


# ------------------------------------------------------------------
# Ablation (skip_critics)
# ------------------------------------------------------------------

def test_ablation_excludes_critic():
    """Removing slop_signals_critic from a vetoed run should lift the veto."""
    critics = [
        _critic("guidelines_critic", 9),
        _critic("architecture_critic", 9),
        _critic("slop_signals_critic", 1),
    ]
    result_with = aggregate(critics)
    result_without = aggregate(critics, skip_critics={"slop_signals_critic"})
    assert result_with.decision != "approve"
    assert result_without.decision == "approve"


def test_ablation_lists_missing_critics():
    critics = [
        _critic("guidelines_critic", 8),
        _critic("architecture_critic", 8),
    ]
    result = aggregate(critics)
    assert "slop_signals_critic" in result.missing_critics


def test_ablation_reweights_present_critics():
    """Two critics present; result should still be deterministic."""
    critics = [
        _critic("guidelines_critic", 8),
        _critic("architecture_critic", 8),
    ]
    result = aggregate(critics)
    # Guidelines + Architecture each at 8 → weighted score > 7 → approve
    assert result.decision == "approve"


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

def test_empty_critics_defaults_to_reject():
    """No critic output is suspicious — flag as slop so a maintainer sees it."""
    result = aggregate([])
    assert result.decision == "reject"
    assert result.confidence == 0.0


def test_per_critic_scores_populated():
    critics = [
        _critic("guidelines_critic", 7),
        _critic("architecture_critic", 6),
        _critic("slop_signals_critic", 8),
    ]
    result = aggregate(critics)
    assert result.per_critic_scores["guidelines_critic"] == 7
    assert result.per_critic_scores["architecture_critic"] == 6
    assert result.per_critic_scores["slop_signals_critic"] == 8


def test_custom_weights():
    """Heavily weighting slop should cause a slop-veto to dominate."""
    critics = [
        _critic("guidelines_critic", 9),
        _critic("architecture_critic", 9),
        _critic("slop_signals_critic", 1),
    ]
    custom = {"guidelines_critic": 0.1, "architecture_critic": 0.1, "slop_signals_critic": 0.8}
    result = aggregate(critics, weights=custom)
    # Even without veto, weighted score should be low → not approve
    assert result.decision != "approve"


def test_findings_appear_in_deciding_factors():
    finding = GuidelinesFinding(severity="critical", category="test", evidence="bad code here")
    details = GuidelinesCriticOutput(score=3, findings=[finding], citations=[])
    critic = CriticOutput(
        critic_name="guidelines_critic",
        verdict="fail",
        reasoning="bad",
        confidence=0.3,
        details=details,
    )
    result = aggregate([critic])
    assert any("bad code here" in f for f in result.deciding_factors)


def test_long_evidence_not_truncated_in_deciding_factors():
    """Regression: deciding_factors used to clip evidence at 120 chars, which
    cut findings mid-word in PR comments. The aggregator must pass the full
    evidence string through; consumers truncate if they need to.
    """
    long_evidence = (
        "PR description explicitly states '🤖 Generated with Claude Code' "
        "confirming automated generation with no human-authored modifications "
        "to the body, and the diff adds an over-engineered UtilityHelper class "
        "that wraps trivial pass-through logic."
    )
    assert len(long_evidence) > 200, "test premise: evidence must exceed the old 120-char cap"
    finding = GuidelinesFinding(severity="major", category="ai_footer", evidence=long_evidence)
    details = GuidelinesCriticOutput(score=2, findings=[finding], citations=[])
    critic = CriticOutput(
        critic_name="slop_signals_critic",
        verdict="fail",
        reasoning="ai footer + over-engineered diff",
        confidence=0.2,
        details=details,
    )
    result = aggregate([critic])
    # The full evidence must survive into deciding_factors — no mid-word cut.
    assert long_evidence in result.deciding_factors
