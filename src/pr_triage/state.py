from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class PRMetadata(BaseModel):
    number: int
    title: str
    body: Optional[str] = None
    author: str
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


class Verdict(BaseModel):
    decision: str  # "approve" | "request_changes" | "reject"
    summary: str
    confidence: float = 0.0


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

    # Phase 2: pipeline outputs
    size_classification: Optional[str] = None  # "trivial"|"small"|"medium"|"large"
    rag_chunks: list[str] = Field(default_factory=list)
    critic_outputs: list[CriticOutput] = Field(default_factory=list)
    aggregate_verdict: Optional[Verdict] = None

    # Phase 3+
    confidence_score: Optional[float] = None
