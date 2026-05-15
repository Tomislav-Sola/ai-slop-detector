"""Deterministic aggregator for multi-critic triage pipeline (Phase 3).

Weights and thresholds are intentionally configurable so ablation experiments
can swap them without touching business logic.
"""
from __future__ import annotations

from pr_triage.state import AggregateResult, CriticOutput

# Critic weights must sum to 1.0.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "guidelines_critic": 0.35,
    "architecture_critic": 0.35,
    "slop_signals_critic": 0.30,
}

# Any critic score at or below this value caps the overall score at _VETO_CAP.
_VETO_THRESHOLD = 2
_VETO_CAP = 4

# Overall score → recommendation thresholds.
_APPROVE_MIN = 7
_REQUEST_CHANGES_MIN = 4  # below this → reject


def aggregate(
    critic_outputs: list[CriticOutput],
    *,
    weights: dict[str, float] | None = None,
    skip_critics: set[str] | None = None,
) -> AggregateResult:
    """Combine critic outputs into a single deterministic verdict.

    Args:
        critic_outputs: outputs from all critics that ran.
        weights: override the default per-critic weights (must still sum to 1.0
                 across the critics that are present after applying skip_critics).
        skip_critics: names to exclude (for ablation experiments).

    Returns:
        AggregateResult with decision, per_critic_scores, and deciding_factors.
    """
    effective_weights = weights if weights is not None else _DEFAULT_WEIGHTS
    skip = skip_critics or set()

    present = {c.critic_name: c for c in critic_outputs if c.critic_name not in skip}
    missing = [name for name in effective_weights if name not in present and name not in skip]

    if not present:
        return AggregateResult(
            decision="request_changes",
            summary="No critic output available.",
            confidence=0.0,
            missing_critics=missing,
        )

    # Build per-critic scores (0–10). Use confidence×10 as a proxy when the
    # critic stores a numeric score in details.score; fall back to confidence.
    per_critic_scores: dict[str, int] = {}
    for name, output in present.items():
        if output.details is not None and hasattr(output.details, "score"):
            per_critic_scores[name] = int(output.details.score)
        else:
            per_critic_scores[name] = round(output.confidence * 10)

    # Renormalise weights to the critics that are actually present.
    active_weight_sum = sum(
        effective_weights.get(name, 0.0) for name in present
    )
    if active_weight_sum <= 0:
        # All present critics have 0 weight — treat equally.
        active_weight_sum = 1.0
        equal_w = 1.0 / len(present)
        normalised = {name: equal_w for name in present}
    else:
        normalised = {
            name: effective_weights.get(name, 0.0) / active_weight_sum
            for name in present
        }

    raw_score = sum(
        per_critic_scores[name] * normalised[name]
        for name in present
    )

    # Veto rule: any individual score ≤ threshold caps the aggregate.
    veto_applied = any(s <= _VETO_THRESHOLD for s in per_critic_scores.values())
    overall_score = min(raw_score, float(_VETO_CAP)) if veto_applied else raw_score

    # Map score to recommendation.
    if overall_score >= _APPROVE_MIN:
        decision = "approve"
    elif overall_score >= _REQUEST_CHANGES_MIN:
        decision = "request_changes"
    else:
        decision = "reject"

    # Collect the most actionable findings across all critics.
    deciding_factors: list[str] = []
    severity_order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    all_findings = []
    for output in present.values():
        if output.details and hasattr(output.details, "findings"):
            for f in output.details.findings:
                all_findings.append((severity_order.get(f.severity, 9), f.evidence[:120]))
    all_findings.sort(key=lambda x: x[0])
    seen: set[str] = set()
    for _, evidence in all_findings[:5]:
        if evidence not in seen:
            deciding_factors.append(evidence)
            seen.add(evidence)

    if veto_applied:
        deciding_factors.insert(
            0,
            f"Veto applied: a critic scored ≤{_VETO_THRESHOLD} (capped at {_VETO_CAP})",
        )

    summary = (
        f"Weighted score {overall_score:.1f}/10 → {decision}. "
        f"Critics: {', '.join(f'{n}={s}' for n, s in per_critic_scores.items())}."
    )
    if missing:
        summary += f" Missing: {', '.join(missing)}."

    confidence = overall_score / 10.0

    return AggregateResult(
        decision=decision,
        summary=summary,
        confidence=confidence,
        per_critic_scores=per_critic_scores,
        deciding_factors=deciding_factors,
        missing_critics=missing,
    )
