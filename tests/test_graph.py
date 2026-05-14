from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pr_triage.claude_client import ClaudeClient
from pr_triage.graph.nodes import (
    _is_trivial,
    classify_size_node,
    emit_verdict_node,
    guidelines_critic_node,
    retrieve_context_node,
)
from pr_triage.graph.pipeline import _check_budget, run_pipeline
from pr_triage.budget import BudgetExceeded
from pr_triage.state import PRMetadata, TriageState

FIXTURES_LLM = Path(__file__).parent / "fixtures" / "llm"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_state(**overrides) -> TriageState:
    defaults = dict(
        repo="owner/repo",
        pr_number=7,
        fetched_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        metadata=PRMetadata(
            number=7,
            title="feat: add widget",
            author="alice",
            created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
            base_branch="main",
            head_branch="feat/widget",
            additions=120,
            deletions=15,
            changed_files=4,
        ),
        files_changed=["src/widget.py", "tests/test_widget.py"],
        raw_diff="diff --git a/src/widget.py\n+class Widget: pass\n" * 30,
    )
    defaults.update(overrides)
    return TriageState(**defaults)


def _fake_client(*responses: str) -> ClaudeClient:
    return ClaudeClient(api_key="x", fake=True, fake_responses=list(responses))


def _mock_rag(chunks: list[str] | None = None) -> MagicMock:
    rag = MagicMock()
    rag.retrieve.return_value = chunks or []
    return rag


# ------------------------------------------------------------------
# _is_trivial heuristic
# ------------------------------------------------------------------

def test_trivial_by_line_count():
    s = _make_state(metadata=PRMetadata(
        number=1, title="t", author="a",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        base_branch="main", head_branch="fix",
        additions=4, deletions=2, changed_files=1,
    ))
    assert _is_trivial(s) is True


def test_trivial_by_docs_only_files():
    s = _make_state(
        files_changed=["README.md", "CHANGELOG.md", ".gitignore", "LICENSE"],
        metadata=PRMetadata(
            number=1, title="t", author="a",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            base_branch="main", head_branch="fix",
            additions=50, deletions=10, changed_files=4,
        ),
    )
    assert _is_trivial(s) is True


def test_not_trivial_with_code_files():
    s = _make_state()
    assert _is_trivial(s) is False


# ------------------------------------------------------------------
# classify_size_node
# ------------------------------------------------------------------

def test_classify_trivial_short_circuits():
    s = _make_state(metadata=PRMetadata(
        number=1, title="t", author="a",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        base_branch="main", head_branch="fix",
        additions=3, deletions=2, changed_files=1,
    ))
    # No fake responses needed — heuristic fires before any LLM call.
    client = _fake_client()
    result = classify_size_node(s, client)
    assert result == {"size_classification": "trivial"}


def test_classify_calls_haiku_for_non_trivial():
    s = _make_state()
    client = _fake_client("large")
    result = classify_size_node(s, client)
    assert result == {"size_classification": "large"}


def test_classify_haiku_fallback_on_unexpected_response():
    s = _make_state()
    client = _fake_client("dunno")
    result = classify_size_node(s, client)
    assert result == {"size_classification": "medium"}


# ------------------------------------------------------------------
# retrieve_context_node
# ------------------------------------------------------------------

def test_retrieve_context_calls_rag():
    s = _make_state()
    rag = _mock_rag(["[CONTRIBUTING.md:0] Use conventional commits"])
    result = retrieve_context_node(s, rag)
    assert len(result["rag_chunks"]) == 1
    rag.retrieve.assert_called_once()


def test_retrieve_context_empty_index_returns_empty():
    s = _make_state()
    rag = _mock_rag([])
    result = retrieve_context_node(s, rag)
    assert result["rag_chunks"] == []


# ------------------------------------------------------------------
# guidelines_critic_node
# ------------------------------------------------------------------

_CRITIC_JSON = json.dumps({
    "score": 7,
    "findings": [{"severity": "major", "category": "testing", "evidence": "no tests"}],
    "citations": ["chunk-0"],
})


def test_guidelines_critic_parses_score():
    s = _make_state(rag_chunks=["[chunk-0] Use tests"])
    client = _fake_client(_CRITIC_JSON)
    result = guidelines_critic_node(s, client)
    output = result["critic_outputs"][0]
    assert output.details.score == 7


def test_guidelines_critic_parses_findings():
    s = _make_state(rag_chunks=["[chunk-0] Use tests"])
    client = _fake_client(_CRITIC_JSON)
    result = guidelines_critic_node(s, client)
    assert len(result["critic_outputs"][0].details.findings) == 1


def test_guidelines_critic_verdict_needs_review():
    s = _make_state(rag_chunks=[])
    client = _fake_client(_CRITIC_JSON)
    result = guidelines_critic_node(s, client)
    assert result["critic_outputs"][0].verdict == "needs_review"


def test_guidelines_critic_strips_markdown_fences():
    fenced = f"```json\n{_CRITIC_JSON}\n```"
    s = _make_state(rag_chunks=[])
    client = _fake_client(fenced)
    result = guidelines_critic_node(s, client)
    assert result["critic_outputs"][0].details.score == 7


# ------------------------------------------------------------------
# emit_verdict_node
# ------------------------------------------------------------------

def test_emit_verdict_trivial():
    s = _make_state(size_classification="trivial")
    result = emit_verdict_node(s)
    assert result["aggregate_verdict"].decision == "approve"


def test_emit_verdict_no_critics():
    s = _make_state(size_classification="medium")
    result = emit_verdict_node(s)
    assert result["aggregate_verdict"].decision == "request_changes"


# ------------------------------------------------------------------
# Budget pre-check
# ------------------------------------------------------------------

def test_budget_precheck_rejects_oversized():
    s = _make_state(raw_diff="x" * 800_000)  # ~200k tokens
    with pytest.raises(BudgetExceeded):
        _check_budget(s, max_tokens=50_000)


def test_budget_precheck_passes_small():
    s = _make_state()
    _check_budget(s, max_tokens=50_000)  # should not raise


# ------------------------------------------------------------------
# End-to-end: run_pipeline with cached fixture (papertriage PR #9)
# ------------------------------------------------------------------

def test_e2e_papertriage_pr9_key_fields():
    """Smoke-test the full pipeline using the cached papertriage PR #9 fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "papertriage_pr9.json"
    pr_data = json.loads(fixture_path.read_text())

    pr = pr_data["pr"]
    state = TriageState(
        repo=pr_data["repo"],
        pr_number=pr_data["pr_number"],
        metadata=PRMetadata(
            number=pr["number"],
            title=pr["title"],
            body=pr["body"],
            author=pr["user_login"],
            created_at=datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00")),
            base_branch=pr["base_ref"],
            head_branch=pr["head_ref"],
            additions=pr["additions"],
            deletions=pr["deletions"],
            changed_files=pr["changed_files"],
            labels=pr["labels"],
            draft=pr["draft"],
            merged=pr["merged"],
        ),
        files_changed=pr_data["files_changed"],
        raw_diff=pr_data.get("raw_diff", ""),
        author_prior_prs=pr_data.get("author_prior_prs", 0),
        contributing_md=pr_data.get("contributing_md"),
        agents_md=pr_data.get("agents_md"),
        recent_merged_titles=pr_data.get("recent_merged_titles", []),
    )

    responses = json.loads((FIXTURES_LLM / "check_Tomislav-Sola__papertriage_pr9.json").read_text())
    client = ClaudeClient(api_key="x", fake=True, fake_responses=responses)
    rag = _mock_rag(["[merged-pr:0] V3: Interactive review + knowledge graph"])

    result = run_pipeline(state, client, rag, max_tokens=50_000)

    # Key-field assertions (not an exact snapshot — survives prompt tweaks)
    assert result.size_classification in {"small", "medium", "large", "trivial"}
    assert result.aggregate_verdict is not None
    assert result.aggregate_verdict.decision in {"approve", "request_changes", "reject"}

    if result.size_classification != "trivial":
        assert len(result.critic_outputs) == 1
        guidelines = result.critic_outputs[0]
        assert guidelines.critic_name == "guidelines_critic"
        assert guidelines.details is not None
        assert 0 <= guidelines.details.score <= 10
        assert isinstance(guidelines.details.findings, list)
        assert len(guidelines.details.findings) > 0
        assert all(isinstance(c, str) for c in guidelines.details.citations)
