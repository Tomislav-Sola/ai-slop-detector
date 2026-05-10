import json
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pr_triage.cli import app
from pr_triage.state import PRMetadata, TriageState

runner = CliRunner()


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


# Single-command Typer apps are invoked without the command name:
# fetch is a proper subcommand: runner.invoke(app, ["fetch", "owner/repo", "7"])


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
