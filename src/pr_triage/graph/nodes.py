from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pr_triage.state import CriticOutput, GuidelinesCriticOutput, GuidelinesFinding, Verdict

if TYPE_CHECKING:
    from pr_triage.claude_client import ClaudeClient
    from pr_triage.rag import RAGIndex
    from pr_triage.state import TriageState

# File extensions that indicate a documentation-only or config-only changeset.
# A PR touching only these is classified as trivial without calling Haiku.
# Covers: prose docs, images, common config formats, lock files, and files
# with no extension (LICENSE, NOTICE, AUTHORS, etc.).
_DOC_AND_CONFIG_SUFFIXES = {
    # Prose / documentation
    ".md", ".rst", ".txt", ".adoc",
    # Config / manifest (no executable logic)
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf",
    ".gitignore", ".gitattributes", ".editorconfig", ".prettierrc", ".eslintrc",
    # Assets
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    # Lock files
    ".lock",
    # No extension — LICENSE, NOTICE, AUTHORS, Makefile variants, etc.
    "",
}

_CLASSIFY_SYSTEM = (
    "You are a PR size classifier. "
    "Classify the PR as exactly one of: small, medium, large.\n"
    "- small:  10–100 changed lines, focused single concern\n"
    "- medium: 100–500 changed lines, or multiple concerns\n"
    "- large:  500+ changed lines, or major architectural change\n"
    "Respond with exactly one word."
)

_CRITIC_SYSTEM = """\
You are a senior code reviewer checking whether this PR follows the project's contributing guidelines.
You are given retrieved guideline snippets — use only those that are actually relevant, and cite them.

Return ONLY valid JSON that matches this exact schema (no markdown fences, no prose):
{
  "score": <integer 0–10>,
  "findings": [
    {
      "severity": "critical" | "major" | "minor" | "info",
      "category": "<string>",
      "evidence": "<exact quote from the PR diff or retrieved context>"
    }
  ],
  "citations": ["<chunk_id>", ...]
}
score 10 = fully compliant; 0 = severely non-compliant.
"""


# ------------------------------------------------------------------
# Node: ingest_pr
# ------------------------------------------------------------------

def ingest_pr_node(state: TriageState) -> dict:
    """Validates that required data is present; no data fetching happens here."""
    if state.metadata is None:
        raise ValueError("ingest_pr: state.metadata is missing")
    return {}


# ------------------------------------------------------------------
# Node: classify_size
# ------------------------------------------------------------------

def classify_size_node(state: TriageState, claude: ClaudeClient) -> dict:
    """Classify PR size as trivial / small / medium / large.

    Two heuristics short-circuit to 'trivial' before any LLM call:
    1. additions + deletions < 10 — genuinely tiny changesets.
    2. All changed files are docs/config only (.md, .rst, .txt, .gitignore,
       LICENSE, etc.) — no code changed so guidelines critique adds no value.
    Otherwise Haiku decides between small / medium / large by reading the diff
    summary and file list.
    """
    if _is_trivial(state):
        return {"size_classification": "trivial"}

    meta = state.metadata
    files_preview = "\n".join(state.files_changed[:30])
    diff_preview = (state.raw_diff or "")[:3000]

    user_msg = (
        f"PR: {meta.title}\n"
        f"{meta.additions} additions, {meta.deletions} deletions across {meta.changed_files} files\n\n"
        f"Files changed:\n{files_preview}\n\n"
        f"Diff (first 3000 chars):\n{diff_preview}"
    )

    from pr_triage.claude_client import MODEL_HAIKU

    raw = claude.complete(
        messages=[{"role": "user", "content": user_msg}],
        model=MODEL_HAIKU,
        max_tokens=16,
        system=_CLASSIFY_SYSTEM,
    )
    size = raw.strip().lower()
    if size not in {"small", "medium", "large"}:
        size = "medium"  # safe fallback if Haiku returns unexpected text
    return {"size_classification": size}


def _is_trivial(state: TriageState) -> bool:
    total_lines = state.metadata.additions + state.metadata.deletions
    if total_lines < 10:
        return True
    if state.files_changed and all(
        Path(f).suffix.lower() in _DOC_AND_CONFIG_SUFFIXES
        for f in state.files_changed
    ):
        return True
    return False


# ------------------------------------------------------------------
# Node: retrieve_context
# ------------------------------------------------------------------

def retrieve_context_node(state: TriageState, rag: RAGIndex) -> dict:
    """Query ChromaDB for the top-8 chunks most relevant to this PR."""
    query = f"{state.metadata.title}\n{state.metadata.body or ''}\n{' '.join(state.files_changed[:10])}"
    chunks = rag.retrieve(state.repo, query, top_k=8)
    return {"rag_chunks": chunks}


# ------------------------------------------------------------------
# Node: guidelines_critic
# ------------------------------------------------------------------

def guidelines_critic_node(state: TriageState, claude: ClaudeClient) -> dict:
    """Run the Sonnet guidelines critic and return structured findings."""
    from pr_triage.claude_client import MODEL_SONNET

    chunks_text = "\n\n".join(state.rag_chunks) if state.rag_chunks else "(no guideline context retrieved)"
    diff_preview = (state.raw_diff or "")[:6000]

    user_msg = (
        f"PR #{state.metadata.number}: {state.metadata.title}\n\n"
        f"PR Description:\n{state.metadata.body or '(none)'}\n\n"
        f"Diff (first 6000 chars):\n{diff_preview}\n\n"
        f"Retrieved guideline context:\n{chunks_text}"
    )

    raw = claude.complete(
        messages=[{"role": "user", "content": user_msg}],
        model=MODEL_SONNET,
        max_tokens=2048,
        system=_CRITIC_SYSTEM,
    )

    details = _parse_critic_json(raw)
    score = details.score
    verdict_str = "pass" if score >= 8 else "needs_review" if score >= 5 else "fail"

    output = CriticOutput(
        critic_name="guidelines_critic",
        verdict=verdict_str,
        reasoning=f"Score {score}/10 with {len(details.findings)} finding(s).",
        confidence=score / 10.0,
        details=details,
    )
    return {"critic_outputs": [output]}


def _parse_critic_json(raw: str) -> GuidelinesCriticOutput:
    """Extract and parse the JSON block the critic is expected to return.

    Strips markdown code fences if the model wraps the response despite
    being told not to.
    """
    text = raw.strip()
    # Strip markdown fences.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Last resort: find the first {...} block.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
        else:
            raise ValueError(f"guidelines_critic returned unparseable JSON:\n{raw[:500]}")

    findings = [
        GuidelinesFinding(
            severity=f.get("severity", "info"),
            category=f.get("category", "unknown"),
            evidence=f.get("evidence", ""),
        )
        for f in data.get("findings", [])
    ]
    return GuidelinesCriticOutput(
        score=int(data.get("score", 5)),
        findings=findings,
        citations=data.get("citations", []),
    )


# ------------------------------------------------------------------
# Node: emit_verdict
# ------------------------------------------------------------------

def emit_verdict_node(state: TriageState) -> dict:
    """Aggregate critic outputs (or trivial fast-path) into a final Verdict."""
    if state.size_classification == "trivial":
        return {
            "aggregate_verdict": Verdict(
                decision="approve",
                summary="Trivial changeset (docs/config only or <10 lines). No critic run.",
                confidence=1.0,
            )
        }

    if not state.critic_outputs:
        return {
            "aggregate_verdict": Verdict(
                decision="request_changes",
                summary="Critic pipeline produced no output.",
                confidence=0.0,
            )
        }

    guidelines = next(
        (c for c in state.critic_outputs if c.critic_name == "guidelines_critic"),
        None,
    )
    if guidelines is None:
        return {
            "aggregate_verdict": Verdict(
                decision="request_changes",
                summary="Guidelines critic output not found.",
                confidence=0.0,
            )
        }

    decision = {
        "pass": "approve",
        "needs_review": "request_changes",
        "fail": "reject",
    }.get(guidelines.verdict, "request_changes")

    score = guidelines.details.score if guidelines.details else 0
    n_findings = len(guidelines.details.findings) if guidelines.details else 0
    n_citations = len(guidelines.details.citations) if guidelines.details else 0

    summary = (
        f"Guidelines critic: {score}/10 ({guidelines.verdict}). "
        f"{n_findings} finding(s), {n_citations} citation(s)."
    )
    return {
        "aggregate_verdict": Verdict(
            decision=decision,
            summary=summary,
            confidence=guidelines.confidence,
        )
    }
