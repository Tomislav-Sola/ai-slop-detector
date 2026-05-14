import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pr_triage.cli import app, _fixture_path
from pr_triage.state import (
    CriticOutput,
    GuidelinesCriticOutput,
    GuidelinesFinding,
    PRMetadata,
    TriageState,
    Verdict,
)

runner = CliRunner()


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------

def _make_state() -> TriageState:
    return TriageState(
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
            additions=20,
            deletions=3,
            changed_files=2,
        ),
        files_changed=["src/widget.py", "tests/test_widget.py"],
        raw_diff="diff --git a/src/widget.py b/src/widget.py\n+class Widget: pass",
    )


def _make_result_state() -> TriageState:
    findings = [GuidelinesFinding(severity="minor", category="docs", evidence="README not updated")]
    details = GuidelinesCriticOutput(score=7, findings=findings, citations=["chunk-0"])
    output = CriticOutput(
        critic_name="guidelines_critic",
        verdict="needs_review",
        reasoning="Score 7/10",
        confidence=0.7,
        details=details,
    )
    state = _make_state()
    return state.model_copy(update={
        "size_classification": "medium",
        "rag_chunks": ["[chunk-0] Use conventional commits"],
        "critic_outputs": [output],
        "aggregate_verdict": Verdict(
            decision="request_changes",
            summary="Guidelines critic: 7/10 (needs_review). 1 finding(s), 1 citation(s).",
            confidence=0.7,
        ),
    })


@pytest.fixture
def llm_fixture(tmp_path) -> Path:
    """Minimal LLM fixture file for --fake CLI tests."""
    responses = [
        "medium",
        json.dumps({"score": 7, "findings": [{"severity": "minor", "category": "docs", "evidence": "test"}], "citations": ["chunk-0"]}),
    ]
    p = tmp_path / "fixture.json"
    p.write_text(json.dumps(responses))
    return p


# ------------------------------------------------------------------
# fetch command (Phase 1 — keep existing tests)
# ------------------------------------------------------------------

def test_fetch_missing_token_exits_nonzero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
        result = runner.invoke(app, ["fetch", "owner/repo", "1"])
    assert result.exit_code == 1


def test_fetch_missing_token_error_message():
    with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
        result = runner.invoke(app, ["fetch", "owner/repo", "1"])
    assert "GITHUB_TOKEN" in result.output


def test_fetch_bad_repo_format_exits_nonzero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
        result = runner.invoke(app, ["fetch", "noslash", "1"])
    assert result.exit_code == 1


def test_fetch_bad_repo_format_error_message():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
        result = runner.invoke(app, ["fetch", "noslash", "1"])
    assert "owner/repo" in result.output


def test_fetch_happy_path_exits_zero():
    state = _make_state()
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
        with patch("pr_triage.cli.GitHubClient") as mock_cls:
            mock_cls.return_value.fetch_pr.return_value = state
            result = runner.invoke(app, ["fetch", "owner/repo", "7"])
    assert result.exit_code == 0


def test_fetch_happy_path_outputs_valid_json():
    state = _make_state()
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
        with patch("pr_triage.cli.GitHubClient") as mock_cls:
            mock_cls.return_value.fetch_pr.return_value = state
            result = runner.invoke(app, ["fetch", "owner/repo", "7"])
    data = json.loads(result.output)
    assert data["repo"] == "owner/repo"
    assert data["pr_number"] == 7
    assert data["metadata"]["title"] == "feat: add widget"


def test_fetch_happy_path_passes_token_to_client():
    state = _make_state()
    with patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}):
        with patch("pr_triage.cli.GitHubClient") as mock_cls:
            mock_cls.return_value.fetch_pr.return_value = state
            runner.invoke(app, ["fetch", "owner/repo", "7"])
    mock_cls.assert_called_once_with(token="secret-token")


def test_fetch_happy_path_passes_pr_number_to_client():
    state = _make_state()
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
        with patch("pr_triage.cli.GitHubClient") as mock_cls:
            mock_cls.return_value.fetch_pr.return_value = state
            runner.invoke(app, ["fetch", "owner/repo", "7"])
    mock_cls.return_value.fetch_pr.assert_called_once_with("owner/repo", 7)


# ------------------------------------------------------------------
# check command
# ------------------------------------------------------------------

def test_check_missing_token_exits_nonzero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
        result = runner.invoke(app, ["check", "owner/repo", "1"])
    assert result.exit_code == 1


def test_check_missing_token_error_message():
    with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
        result = runner.invoke(app, ["check", "owner/repo", "1"])
    assert "GITHUB_TOKEN" in result.output


def test_check_bad_repo_format_exits_nonzero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}):
        result = runner.invoke(app, ["check", "noslash", "1"])
    assert result.exit_code == 1


def test_check_missing_api_key_exits_nonzero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake", "ANTHROPIC_API_KEY": ""}):
        result = runner.invoke(app, ["check", "owner/repo", "1"])
    assert result.exit_code == 1


def test_check_missing_api_key_error_message():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake", "ANTHROPIC_API_KEY": ""}):
        result = runner.invoke(app, ["check", "owner/repo", "1"])
    assert "ANTHROPIC_API_KEY" in result.output


def test_check_fake_missing_fixture_exits_nonzero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.cli.GitHubClient") as mock_gh, \
         patch("pr_triage.cli._fixture_path", return_value=Path("/nonexistent/fixture.json")):
        mock_gh.return_value.fetch_pr.return_value = _make_state()
        result = runner.invoke(app, ["--fake", "check", "owner/repo", "7"])
    assert result.exit_code == 1


def test_check_fake_happy_path_exits_zero(llm_fixture):
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.cli.GitHubClient") as mock_gh, \
         patch("pr_triage.rag.RAGIndex"), \
         patch("pr_triage.cli._fixture_path", return_value=llm_fixture), \
         patch("pr_triage.graph.pipeline.run_pipeline", return_value=_make_result_state()):
        mock_gh.return_value.fetch_pr.return_value = _make_state()
        result = runner.invoke(app, ["--fake", "check", "owner/repo", "7"])
    assert result.exit_code == 0


def test_check_fake_human_readable_shows_score(llm_fixture):
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.cli.GitHubClient") as mock_gh, \
         patch("pr_triage.rag.RAGIndex"), \
         patch("pr_triage.cli._fixture_path", return_value=llm_fixture), \
         patch("pr_triage.graph.pipeline.run_pipeline", return_value=_make_result_state()):
        mock_gh.return_value.fetch_pr.return_value = _make_state()
        result = runner.invoke(app, ["--fake", "check", "owner/repo", "7"])
    assert "7/10" in result.output
    assert "needs_review" in result.output


def test_check_fake_json_flag_outputs_valid_json(llm_fixture):
    result_state = _make_result_state()
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.cli.GitHubClient") as mock_gh, \
         patch("pr_triage.rag.RAGIndex"), \
         patch("pr_triage.cli._fixture_path", return_value=llm_fixture), \
         patch("pr_triage.graph.pipeline.run_pipeline", return_value=result_state):
        mock_gh.return_value.fetch_pr.return_value = _make_state()
        result = runner.invoke(app, ["--fake", "check", "owner/repo", "7", "--json"])
    # CliRunner mixes stderr into output; skip the progress line before the JSON object.
    json_text = result.output[result.output.index("{"):]
    data = json.loads(json_text)
    assert data["pr_number"] == 7
    assert data["size_classification"] == "medium"
    assert data["aggregate_verdict"]["decision"] == "request_changes"


def test_check_trivial_state_shows_approve(llm_fixture):
    trivial = _make_state().model_copy(update={
        "size_classification": "trivial",
        "aggregate_verdict": Verdict(
            decision="approve",
            summary="Trivial changeset (docs/config only or <10 lines). No critic run.",
            confidence=1.0,
        ),
    })
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.cli.GitHubClient") as mock_gh, \
         patch("pr_triage.rag.RAGIndex"), \
         patch("pr_triage.cli._fixture_path", return_value=llm_fixture), \
         patch("pr_triage.graph.pipeline.run_pipeline", return_value=trivial):
        mock_gh.return_value.fetch_pr.return_value = _make_state()
        result = runner.invoke(app, ["--fake", "check", "owner/repo", "7"])
    assert "trivial" in result.output.lower()
    assert "approve" in result.output.lower()


# ------------------------------------------------------------------
# index command
# ------------------------------------------------------------------

def test_index_missing_token_exits_nonzero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
        result = runner.invoke(app, ["index", "owner/repo"])
    assert result.exit_code == 1


def test_index_bad_repo_format_exits_nonzero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}):
        result = runner.invoke(app, ["index", "noslash"])
    assert result.exit_code == 1


def test_index_happy_path_exits_zero():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.cli.GitHubClient") as mock_gh, \
         patch("pr_triage.rag.RAGIndex") as mock_rag:
        mock_gh.return_value.fetch_repo_context.return_value = {
            "contributing_md": "Use conventional commits.",
            "agents_md": None,
            "merged_prs": [],
        }
        mock_rag.return_value.index_repo.return_value = 5
        result = runner.invoke(app, ["index", "owner/repo"])
    assert result.exit_code == 0


def test_index_happy_path_reports_chunk_count():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.cli.GitHubClient") as mock_gh, \
         patch("pr_triage.rag.RAGIndex") as mock_rag:
        mock_gh.return_value.fetch_repo_context.return_value = {
            "contributing_md": None,
            "agents_md": None,
            "merged_prs": [],
        }
        mock_rag.return_value.index_repo.return_value = 12
        result = runner.invoke(app, ["index", "owner/repo"])
    assert "12" in result.output


# ------------------------------------------------------------------
# _fixture_path helper
# ------------------------------------------------------------------

def test_fixture_path_replaces_slash_with_double_underscore():
    p = _fixture_path("owner/repo", 42)
    assert "owner__repo" in p.name


def test_fixture_path_includes_pr_number():
    p = _fixture_path("owner/repo", 42)
    assert "pr42" in p.name


# ------------------------------------------------------------------
# harvest command
# ------------------------------------------------------------------

def test_harvest_requires_github_token():
    with patch.dict(os.environ, {}, clear=True):
        result = runner.invoke(app, ["harvest", "owner/repo"])
    assert result.exit_code == 1
    assert "GITHUB_TOKEN" in result.output


def test_harvest_rejects_invalid_repo_format():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.harvest.estimate_harvest_calls", return_value={"estimated_new": 0, "already_cached": 0}):
        result = runner.invoke(app, ["harvest", "notarepo"])
    assert result.exit_code == 1
    assert "owner/repo" in result.output


def test_harvest_yes_flag_skips_confirmation(tmp_path):
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}), \
         patch("pr_triage.harvest.harvest_repo", return_value=(5, 0)) as mock_harvest:
        result = runner.invoke(
            app,
            ["harvest", "owner/repo", "--yes", "--out-dir", str(tmp_path)],
        )
    assert result.exit_code == 0
    assert "5" in result.output
    mock_harvest.assert_called_once()


# ------------------------------------------------------------------
# prelabel command
# ------------------------------------------------------------------

def test_prelabel_requires_existing_candidates_dir():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}):
        result = runner.invoke(app, ["prelabel", "--candidates-dir", "/nonexistent/path"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_prelabel_happy_path(tmp_path):
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir()
    out_path = tmp_path / "pre_labels.jsonl"

    with patch("pr_triage.prelabel.prelabel_dir", return_value=7) as mock_pl:
        result = runner.invoke(
            app,
            ["prelabel", "--candidates-dir", str(cand_dir), "--out", str(out_path)],
        )
    assert result.exit_code == 0
    assert "7" in result.output
    mock_pl.assert_called_once()


# ------------------------------------------------------------------
# golden-build command
# ------------------------------------------------------------------

def test_golden_build_exits_on_error(tmp_path):
    with patch("pr_triage.golden.build_golden_set") as mock_build:
        from pr_triage.golden import GoldenBuildError
        mock_build.side_effect = GoldenBuildError("missing labels file")
        result = runner.invoke(
            app,
            ["golden-build", "--labels", str(tmp_path / "missing.jsonl")],
        )
    assert result.exit_code == 1
    assert "missing labels file" in result.output


def test_golden_build_happy_path(tmp_path):
    with patch("pr_triage.golden.build_golden_set", return_value={
        "total": 30, "approve": 10, "request_changes": 15, "reject": 5,
    }):
        result = runner.invoke(
            app,
            ["golden-build", "--labels", str(tmp_path / "labels.jsonl")],
        )
    assert result.exit_code == 0
    assert "30" in result.output
