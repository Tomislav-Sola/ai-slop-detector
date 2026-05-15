from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pr_triage.state import (
    AggregateResult,
    CriticOutput,
    GuidelinesCriticOutput,
    GuidelinesFinding,
    SloppinessFeatures,
    Verdict,
)

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

def guidelines_critic_node(state: TriageState, claude: ClaudeClient, *, model: str | None = None) -> dict:
    """Guidelines critic — Sonnet in production, overridable for eval."""
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
        model=model or MODEL_SONNET,
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
# Node: architecture_critic (B1)
# ------------------------------------------------------------------

_ARCH_CRITIC_SYSTEM = """\
You are an architecture reviewer evaluating whether this PR fits the project's existing patterns.

Given the diff and retrieved context (recent merged PRs, contributing guidelines), assess:
1. Does the change follow established naming conventions, module structure, and abstractions?
2. Is the abstraction level appropriate for the problem size (no over-engineering, no under-abstraction)?
3. Are new patterns introduced where existing ones already cover the need?
4. Are there architectural smells: unused code paths, premature generalization, dead code added?

Return ONLY valid JSON (no markdown fences, no prose):
{
  "score": <integer 0-10>,
  "findings": [
    {
      "severity": "critical" | "major" | "minor" | "info",
      "category": "<string>",
      "evidence": "<exact quote from diff or context>"
    }
  ],
  "citations": ["<chunk_id>", ...]
}
score 10 = architecture fully consistent with codebase; 0 = fundamentally misaligned.
"""


def architecture_critic_node(state: TriageState, claude: ClaudeClient, *, model: str | None = None) -> dict:
    """Architecture critic — Sonnet in production, overridable for eval."""
    from pr_triage.claude_client import MODEL_SONNET

    chunks_text = "\n\n".join(state.rag_chunks) if state.rag_chunks else "(no context retrieved)"
    diff_preview = (state.raw_diff or "")[:6000]

    user_msg = (
        f"PR #{state.metadata.number}: {state.metadata.title}\n\n"
        f"PR Description:\n{state.metadata.body or '(none)'}\n\n"
        f"Files changed: {', '.join(state.files_changed[:20])}\n\n"
        f"Diff (first 6000 chars):\n{diff_preview}\n\n"
        f"Retrieved project context:\n{chunks_text}"
    )

    raw = claude.complete(
        messages=[{"role": "user", "content": user_msg}],
        model=model or MODEL_SONNET,
        max_tokens=2048,
        system=_ARCH_CRITIC_SYSTEM,
    )

    details = _parse_critic_json(raw)
    score = details.score
    verdict_str = "pass" if score >= 8 else "needs_review" if score >= 5 else "fail"

    output = CriticOutput(
        critic_name="architecture_critic",
        verdict=verdict_str,
        reasoning=f"Architecture score {score}/10 with {len(details.findings)} finding(s).",
        confidence=score / 10.0,
        details=details,
    )
    return {"critic_outputs": [output]}


# ------------------------------------------------------------------
# Node: slop_signals_critic (B2)
# ------------------------------------------------------------------

_SLOP_CRITIC_SYSTEM = """\
You are a slop detector evaluating whether this PR shows signs of low-effort, AI-generated, or cargo-cult contributions.

You are given the diff, PR description, and a pre-computed heuristic feature vector.
Assess:
1. Does the diff solve a real problem in a focused way, or does it add bloat disproportionate to scope?
2. Are generic AI-typical phrases present in the description without specific technical content?
3. Does the PR reinvent existing functionality rather than reusing it?
4. Do the heuristic features (see below) indicate quality issues?

Return ONLY valid JSON (no markdown fences, no prose):
{
  "score": <integer 0-10>,
  "findings": [
    {
      "severity": "critical" | "major" | "minor" | "info",
      "category": "<string>",
      "evidence": "<exact quote from diff or PR description>"
    }
  ],
  "citations": []
}
score 10 = high quality, no slop signals; 0 = clear slop (AI-generated boilerplate, zero substance).
"""

# Phrases frequently found in AI-generated PR descriptions.
_AI_PHRASES = [
    "this pr ", "this pull request ", "i have implemented", "i have added",
    "feel free to ", "let me know if ", "hope this helps", "happy to make",
    "easy to understand", "improve code quality", "improve readability",
    "enhance performance", "various improvements", "minor improvements",
    "several improvements", "best practices", "clean code", "as per your request",
]


def _compute_sloppiness_features(state: TriageState) -> SloppinessFeatures:
    """Extract heuristic slop signals from the raw diff and PR metadata."""
    diff = state.raw_diff or ""
    body = (state.metadata.body or "").lower()
    added_lines = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]

    duplicate_line_ratio = _duplicate_ratio(added_lines)
    todo_fixme_count = sum(
        1 for ln in added_lines
        if any(kw in ln.lower() for kw in ("todo", "fixme", "hack", "xxx"))
    )
    debug_print_count = sum(
        1 for ln in added_lines
        if any(kw in ln.lower() for kw in ("print(", "console.log(", "debugger", "pdb.set_trace"))
    )
    magic_number_count = _count_magic_numbers(added_lines)
    generic_phrases = sum(1 for phrase in _AI_PHRASES if phrase in body)
    long_function_count = _count_long_added_functions(added_lines)

    return SloppinessFeatures(
        duplicate_line_ratio=duplicate_line_ratio,
        long_function_count=long_function_count,
        todo_fixme_count=todo_fixme_count,
        debug_print_count=debug_print_count,
        magic_number_count=magic_number_count,
        missing_docstring_count=generic_phrases,  # repurposed: AI phrase count
    )


def _duplicate_ratio(lines: list[str]) -> float:
    stripped = [ln.strip() for ln in lines if ln.strip()]
    if not stripped:
        return 0.0
    unique = len(set(stripped))
    return 1.0 - unique / len(stripped)


def _count_magic_numbers(lines: list[str]) -> int:
    import re
    count = 0
    for ln in lines:
        # Raw numeric literals not assigned to a named constant
        if re.search(r"(?<![a-zA-Z_\d])\d{2,}(?!\s*[=:,\)])", ln):
            count += 1
    return count


def _count_long_added_functions(lines: list[str]) -> int:
    """Count function/method definitions in added lines that span >50 added lines."""
    import re
    func_starts: list[int] = []
    for i, ln in enumerate(lines):
        if re.match(r"\s*(def |function |async def |\w+\s*\()", ln):
            func_starts.append(i)
    # Simple heuristic: if total added lines per function block > 50
    count = 0
    for i, start in enumerate(func_starts):
        end = func_starts[i + 1] if i + 1 < len(func_starts) else len(lines)
        if end - start > 50:
            count += 1
    return count


def slop_signals_critic_node(state: TriageState, claude: ClaudeClient, *, model: str | None = None) -> dict:
    """Slop critic — Haiku in production (heuristics carry most weight), overridable for eval."""
    from pr_triage.claude_client import MODEL_HAIKU

    features = _compute_sloppiness_features(state)

    feature_summary = (
        f"Heuristic features:\n"
        f"  duplicate_line_ratio: {features.duplicate_line_ratio:.2f}\n"
        f"  long_function_count: {features.long_function_count}\n"
        f"  todo_fixme_count: {features.todo_fixme_count}\n"
        f"  debug_print_count: {features.debug_print_count}\n"
        f"  magic_number_count: {features.magic_number_count}\n"
        f"  ai_phrase_count (in description): {features.missing_docstring_count}\n"
    )

    diff_preview = (state.raw_diff or "")[:4000]
    user_msg = (
        f"PR #{state.metadata.number}: {state.metadata.title}\n\n"
        f"PR Description:\n{state.metadata.body or '(none)'}\n\n"
        f"{feature_summary}\n"
        f"Diff (first 4000 chars):\n{diff_preview}"
    )

    raw = claude.complete(
        messages=[{"role": "user", "content": user_msg}],
        model=model or MODEL_HAIKU,
        max_tokens=1024,
        system=_SLOP_CRITIC_SYSTEM,
    )

    details = _parse_critic_json(raw)
    score = details.score
    verdict_str = "pass" if score >= 8 else "needs_review" if score >= 5 else "fail"

    output = CriticOutput(
        critic_name="slop_signals_critic",
        verdict=verdict_str,
        reasoning=f"Slop score {score}/10 with {len(details.findings)} finding(s).",
        confidence=score / 10.0,
        details=details,
    )
    return {
        "sloppiness_features": features,
        "critic_outputs": [output],
    }


# ------------------------------------------------------------------
# Node: aggregate (replaces emit_verdict for Phase 3+)
# ------------------------------------------------------------------

def aggregate_node(state: TriageState) -> dict:
    """Deterministic multi-critic aggregator node.

    Combines all critic outputs via the aggregator module.
    Writes both aggregate_result (Phase 3) and the backward-compatible
    aggregate_verdict (Phase 2 field).
    """
    from pr_triage.aggregator import aggregate

    result = aggregate(state.critic_outputs)

    verdict = Verdict(
        decision=result.decision,
        summary=result.summary,
        confidence=result.confidence,
    )
    return {
        "aggregate_verdict": verdict,
        "aggregate_result": result,
    }


# Keep emit_verdict_node as an alias for backward compatibility with Phase 2 tests.
emit_verdict_node = aggregate_node
