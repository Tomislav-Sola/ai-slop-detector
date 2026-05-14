from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pr_triage.harvest import (
    PRCandidate,
    candidate_path,
    harvest_repo,
    parse_linked_issues,
)


# ------------------------------------------------------------------
# parse_linked_issues
# ------------------------------------------------------------------

def test_parse_keyword_close():
    result = parse_linked_issues("Closes #42", None)
    assert result == [{"number": 42, "repo": None}]


def test_parse_keyword_fixes():
    result = parse_linked_issues(None, "This fixes #7 in the pipeline.")
    assert result == [{"number": 7, "repo": None}]


def test_parse_keyword_resolves():
    result = parse_linked_issues(None, "Resolves #123")
    assert result == [{"number": 123, "repo": None}]


def test_parse_cross_repo_ref():
    result = parse_linked_issues(None, "Fixes owner/other#99")
    assert len(result) == 1
    assert result[0]["number"] == 99
    assert result[0]["repo"] == "owner/other"


def test_parse_bare_ref():
    result = parse_linked_issues(None, "See #55 for context.")
    assert {"number": 55, "repo": None} in result


def test_parse_deduplicates():
    result = parse_linked_issues("Closes #42", "Also #42 is relevant.")
    numbers = [r["number"] for r in result]
    assert numbers.count(42) == 1


def test_parse_both_title_and_body():
    result = parse_linked_issues("Fix #10", "Closes #20")
    numbers = {r["number"] for r in result}
    assert 10 in numbers
    assert 20 in numbers


def test_parse_empty_inputs():
    assert parse_linked_issues(None, None) == []


def test_parse_no_refs():
    assert parse_linked_issues("refactor: cleanup", "No issue references here.") == []


# ------------------------------------------------------------------
# candidate_path
# ------------------------------------------------------------------

def test_candidate_path_slash_to_double_underscore(tmp_path):
    p = candidate_path(tmp_path, "owner/repo", 5)
    assert p.name == "owner__repo_pr5.json"


def test_candidate_path_inside_out_dir(tmp_path):
    p = candidate_path(tmp_path, "owner/repo", 5)
    assert p.parent == tmp_path


# ------------------------------------------------------------------
# harvest_repo — skipping existing files
# ------------------------------------------------------------------

def _make_fake_pr(number: int, title: str = "PR title") -> MagicMock:
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.body = None
    pr.user.login = "alice"
    pr.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    pr.updated_at = datetime(2024, 1, 2, tzinfo=timezone.utc)
    pr.merged_at = None
    pr.base.ref = "main"
    pr.head.ref = "fix/thing"
    pr.additions = 10
    pr.deletions = 5
    pr.changed_files = 2
    pr.labels = []
    pr.draft = False
    pr.merged = False
    pr.diff_url = "https://example.com/diff"
    pr.get_files.return_value = []
    return pr


def test_harvest_skips_existing_file(tmp_path):
    existing = candidate_path(tmp_path, "owner/repo", 1)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(json.dumps({"repo": "owner/repo", "pr_number": 1}))

    fake_pr = _make_fake_pr(1)
    mock_repo = MagicMock()
    mock_repo.get_pulls.return_value = [fake_pr]

    with patch("pr_triage.harvest.Github") as mock_gh:
        mock_gh.return_value.get_repo.return_value = mock_repo
        new_count, skipped = harvest_repo(
            "owner/repo", "token", tmp_path, states=["open"], re_record=False
        )

    assert new_count == 0
    assert skipped == 1


def test_harvest_saves_new_pr(tmp_path):
    fake_pr = _make_fake_pr(7)
    mock_repo = MagicMock()
    mock_repo.get_pulls.return_value = [fake_pr]

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value="diff content"):
        mock_gh.return_value.get_repo.return_value = mock_repo
        new_count, skipped = harvest_repo(
            "owner/repo", "token", tmp_path, states=["open"]
        )

    assert new_count == 1
    assert skipped == 0
    dest = candidate_path(tmp_path, "owner/repo", 7)
    assert dest.exists()
    data = json.loads(dest.read_text())
    assert data["pr_number"] == 7
    assert data["raw_diff"] == "diff content"
    assert "_meta" in data


def test_harvest_re_record_overwrites(tmp_path):
    existing = candidate_path(tmp_path, "owner/repo", 3)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(json.dumps({"pr_number": 3, "title": "old"}))

    fake_pr = _make_fake_pr(3, title="new title")
    mock_repo = MagicMock()
    mock_repo.get_pulls.return_value = [fake_pr]

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        mock_gh.return_value.get_repo.return_value = mock_repo
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["open"], re_record=True
        )

    assert new_count == 1
    data = json.loads(existing.read_text())
    assert data["title"] == "new title"


def test_harvest_respects_max_prs(tmp_path):
    prs = [_make_fake_pr(i) for i in range(1, 6)]
    mock_repo = MagicMock()
    mock_repo.get_pulls.return_value = prs

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        mock_gh.return_value.get_repo.return_value = mock_repo
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["open"], max_prs=3
        )

    assert new_count <= 3


def test_harvest_fetches_linked_issue_titles(tmp_path):
    fake_pr = _make_fake_pr(10)
    fake_pr.body = "Closes #42"
    mock_repo = MagicMock()
    mock_issue = MagicMock()
    mock_issue.title = "Fix the thing"
    mock_repo.get_pulls.return_value = [fake_pr]
    mock_repo.get_issue.return_value = mock_issue

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        mock_gh.return_value.get_repo.return_value = mock_repo
        harvest_repo("owner/repo", "token", tmp_path, states=["open"])

    dest = candidate_path(tmp_path, "owner/repo", 10)
    data = json.loads(dest.read_text())
    linked = data["linked_issues"]
    assert len(linked) >= 1
    same_repo = [li for li in linked if li["repo"] is None and li["number"] == 42]
    assert len(same_repo) == 1
    assert same_repo[0]["title"] == "Fix the thing"
