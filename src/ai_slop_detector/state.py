from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import BaseModel, Field


class PRMetadata(BaseModel):
    number: int
    title: str
    body: Optional[str] = None
    author: str
    # GitHub author_association — one of OWNER/MEMBER/COLLABORATOR/CONTRIBUTOR/NONE
    # or None if not captured. Strong signal for trust-weighting drive-by caps.
    author_association: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    base_branch: str
    head_branch: str
    additions: int
    deletions: int
    changed_files: int
    labels: list[str] = Field(default_factory=list)
    draft: bool = False
    merged: bool = False
    mergeable: Optional[bool] = None


class GuidelinesFinding(BaseModel):
    severity: str  # "critical" | "major" | "minor" | "info"
    category: str
    evidence: str  # exact quote from the PR diff or repo context


class GuidelinesCriticOutput(BaseModel):
    score: int  # 0-10
    findings: list[GuidelinesFinding]
    citations: list[str]  # chunk IDs actually used by the critic


class CriticOutput(BaseModel):
    critic_name: str
    verdict: str  # "pass" | "fail" | "needs_review"
    reasoning: str
    confidence: float = 0.0
    details: Optional[GuidelinesCriticOutput] = None


class SloppinessFeatures(BaseModel):
    """Heuristic features extracted from the diff for the slop-signals critic."""
    duplicate_line_ratio: float = 0.0
    long_function_count: int = 0     # functions/methods > 50 lines added
    todo_fixme_count: int = 0        # TODO/FIXME/HACK lines added
    debug_print_count: int = 0       # debug print/console.log lines added
    magic_number_count: int = 0      # raw numeric literals added
    missing_docstring_count: int = 0  # public functions/classes added without docstrings


class Verdict(BaseModel):
    decision: str  # "approve" | "request_changes" | "reject"
    summary: str
    confidence: float = 0.0


class AggregateResult(BaseModel):
    """Richer aggregate output produced by the Phase 3 deterministic aggregator."""
    decision: str  # "approve" | "request_changes" | "reject"
    summary: str
    confidence: float = 0.0
    per_critic_scores: dict[str, int] = Field(default_factory=dict)
    deciding_factors: list[str] = Field(default_factory=list)
    missing_critics: list[str] = Field(default_factory=list)


class TriageState(BaseModel):
    # Identity
    repo: str
    pr_number: int
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # Phase 1: ingested GitHub data
    metadata: PRMetadata
    raw_diff: Optional[str] = None
    files_changed: list[str] = Field(default_factory=list)
    author_prior_prs: int = 0
    contributing_md: Optional[str] = None
    agents_md: Optional[str] = None
    recent_merged_titles: list[str] = Field(default_factory=list)
    # Optional review-state fields. Available in golden fixtures and from
    # GitHub for closed/in-progress PRs; empty for cold first-look mode.
    closed_at: Optional[datetime] = None
    issue_comments: list[dict] = Field(default_factory=list)
    review_comments: list[dict] = Field(default_factory=list)
    bot_comments: list[dict] = Field(default_factory=list)

    # Phase 2: pipeline outputs
    size_classification: Optional[str] = None  # "trivial"|"small"|"medium"|"large"
    rag_chunks: list[str] = Field(default_factory=list)
    # operator.add reducer enables parallel fan-in from multiple critic nodes
    critic_outputs: Annotated[list[CriticOutput], operator.add] = Field(default_factory=list)
    aggregate_verdict: Optional[Verdict] = None

    # Phase 3+
    sloppiness_features: Optional[SloppinessFeatures] = None
    aggregate_result: Optional[AggregateResult] = None
    confidence_score: Optional[float] = None
