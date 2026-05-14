from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pr_triage.harvest import (
    PRCandidate,
    candidate_path,
    harvest_repo,
    parse_linked_issues,
)

_NOW = datetime.now(tz=timezone.utc)
_OLD = _NOW - timedelta(days=30)   # safely past the 14-day settle window
_RECENT = _NOW - timedelta(days=2)  # within the 14-day window


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


def test_parse_cross_repo_shortform():
    result = parse_linked_issues(None, "Fixes owner/other#99")
    assert len(result) == 1
    assert result[0]["number"] == 99
    assert result[0]["repo"] == "owner/other"


def test_parse_full_github_url_cross_repo():
    result = parse_linked_issues(
        None,
        "Closes https://github.com/astral-sh/ty/issues/1950",
        repo_name="astral-sh/ruff",
    )
    assert len(result) == 1
    assert result[0]["number"] == 1950
    assert result[0]["repo"] == "astral-sh/ty"


def test_parse_full_github_url_same_repo():
    result = parse_linked_issues(
        None,
        "Fixes https://github.com/owner/repo/issues/42",
        repo_name="owner/repo",
    )
    assert len(result) == 1
    assert result[0]["number"] == 42
    assert result[0]["repo"] is None


def test_parse_full_url_without_repo_name_stored_as_cross_repo():
    # Without repo_name hint, any URL is treated as a (potentially cross-repo) ref
    result = parse_linked_issues(
        None,
        "Closes https://github.com/owner/repo/issues/42",
    )
    assert len(result) == 1
    assert result[0]["number"] == 42
    assert result[0]["repo"] == "owner/repo"


def test_parse_bare_ref():
    result = parse_linked_issues(None, "See #55 for context.")
    assert {"number": 55, "repo": None} in result


def test_parse_deduplicates():
    result = parse_linked_issues("Closes #42", "Also #42 is relevant.")
    numbers = [r["number"] for r in result]
    assert numbers.count(42) == 1


def test_parse_deduplicates_url_and_shortform():
    # Same issue referenced as URL and shortform — should appear once
    result = parse_linked_issues(
        None,
        "Closes #42 and also https://github.com/owner/repo/issues/42",
        repo_name="owner/repo",
    )
    assert len([r for r in result if r["number"] == 42 and r["repo"] is None]) == 1


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
# Helpers for harvest_repo tests
# ------------------------------------------------------------------

def _make_fake_pr(number: int, title: str = "PR title", *, closed_at=None) -> MagicMock:
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.body = None
    pr.user.login = "alice"
    pr.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    pr.updated_at = datetime(2024, 1, 2, tzinfo=timezone.utc)
    pr.closed_at = closed_at if closed_at is not None else _OLD
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
    pr.get_issue_comments.return_value = []
    pr.get_review_comments.return_value = []
    # raw_data used for settle-time check
    pr.raw_data = {"closed_at": _OLD.isoformat() if closed_at is None else (closed_at.isoformat() if closed_at else None)}
    return pr


def _setup_mock_gh(mock_gh_cls, prs: list, search_total: int = 0) -> MagicMock:
    mock_repo = MagicMock()
    mock_repo.get_pulls.return_value = prs
    mock_gh_cls.return_value.get_repo.return_value = mock_repo
    mock_gh_cls.return_value.search_issues.return_value.totalCount = search_total
    return mock_repo


# ------------------------------------------------------------------
# harvest_repo — core behavior
# ------------------------------------------------------------------

def test_harvest_skips_existing_file(tmp_path):
    existing = candidate_path(tmp_path, "owner/repo", 1)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(json.dumps({"repo": "owner/repo", "pr_number": 1}))

    with patch("pr_triage.harvest.Github") as mock_gh:
        _setup_mock_gh(mock_gh, [_make_fake_pr(1)])
        new_count, skipped = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"], re_record=False
        )

    assert new_count == 0
    assert skipped == 1


def test_harvest_saves_new_pr(tmp_path):
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value="diff content"):
        _setup_mock_gh(mock_gh, [_make_fake_pr(7)], search_total=1)
        new_count, skipped = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"]
        )

    assert new_count == 1
    assert skipped == 0
    dest = candidate_path(tmp_path, "owner/repo", 7)
    assert dest.exists()
    data = json.loads(dest.read_text())
    assert data["pr_number"] == 7
    assert data["raw_diff"] == "diff content"
    assert "_meta" in data
    # New schema fields
    assert "issue_comments" in data
    assert "bot_comments" in data
    assert "review_comments" in data
    assert "closed_at" in data
    assert "author_prior_prs_in_repo" in data


def test_harvest_re_record_overwrites(tmp_path):
    existing = candidate_path(tmp_path, "owner/repo", 3)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(json.dumps({"pr_number": 3, "title": "old"}))

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(3, title="new title")])
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"], re_record=True
        )

    assert new_count == 1
    data = json.loads(existing.read_text())
    assert data["title"] == "new title"


def test_harvest_respects_max_prs(tmp_path):
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(i) for i in range(1, 6)])
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"], max_prs=3
        )

    assert new_count <= 3


# ------------------------------------------------------------------
# Settle-time filter
# ------------------------------------------------------------------

def test_harvest_skips_recent_pr(tmp_path):
    recent_pr = _make_fake_pr(99, closed_at=_RECENT)
    recent_pr.raw_data = {"closed_at": _RECENT.isoformat()}

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [recent_pr])
        new_count, skipped = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"], min_age_days=14
        )

    assert new_count == 0
    assert skipped == 1
    assert not candidate_path(tmp_path, "owner/repo", 99).exists()


def test_harvest_keeps_old_pr(tmp_path):
    old_pr = _make_fake_pr(88, closed_at=_OLD)
    old_pr.raw_data = {"closed_at": _OLD.isoformat()}

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [old_pr])
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"], min_age_days=14
        )

    assert new_count == 1


def test_harvest_settle_filter_disabled_with_zero(tmp_path):
    recent_pr = _make_fake_pr(99, closed_at=_RECENT)
    recent_pr.raw_data = {"closed_at": _RECENT.isoformat()}

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [recent_pr])
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"], min_age_days=0
        )

    assert new_count == 1


# ------------------------------------------------------------------
# Linked issues
# ------------------------------------------------------------------

def test_harvest_fetches_linked_issue_titles(tmp_path):
    fake_pr = _make_fake_pr(10)
    fake_pr.body = "Closes #42"

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        mock_repo = _setup_mock_gh(mock_gh, [fake_pr])
        mock_issue = MagicMock()
        mock_issue.title = "Fix the thing"
        mock_repo.get_issue.return_value = mock_issue
        harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    data = json.loads(candidate_path(tmp_path, "owner/repo", 10).read_text())
    linked = data["linked_issues"]
    assert len(linked) >= 1
    same_repo = [li for li in linked if li["repo"] is None and li["number"] == 42]
    assert len(same_repo) == 1
    assert same_repo[0]["title"] == "Fix the thing"


def test_harvest_captures_cross_repo_url(tmp_path):
    fake_pr = _make_fake_pr(11)
    fake_pr.body = "Closes https://github.com/other-org/other-repo/issues/99"

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [fake_pr])
        harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    data = json.loads(candidate_path(tmp_path, "owner/repo", 11).read_text())
    linked = data["linked_issues"]
    cross = [li for li in linked if li["repo"] == "other-org/other-repo"]
    assert len(cross) == 1
    assert cross[0]["number"] == 99
    assert cross[0]["title"] is None  # not fetched for cross-repo


# ------------------------------------------------------------------
# Comments: bot filtering
# ------------------------------------------------------------------

def test_harvest_splits_bot_and_human_comments(tmp_path):
    fake_pr = _make_fake_pr(20)

    mock_human = MagicMock()
    mock_human.user.login = "maintainer"
    mock_human.author_association = "MEMBER"
    mock_human.body = "LGTM"
    mock_human.created_at = _OLD

    mock_bot = MagicMock()
    mock_bot.user.login = "ci-bot[bot]"
    mock_bot.author_association = "NONE"
    mock_bot.body = "All checks passed"
    mock_bot.created_at = _OLD

    fake_pr.get_issue_comments.return_value = [mock_human, mock_bot]

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [fake_pr])
        harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    data = json.loads(candidate_path(tmp_path, "owner/repo", 20).read_text())
    assert len(data["issue_comments"]) == 1
    assert data["issue_comments"][0]["user"] == "maintainer"
    assert len(data["bot_comments"]) == 1
    assert data["bot_comments"][0]["user"] == "ci-bot[bot]"


def test_harvest_includes_review_comments(tmp_path):
    fake_pr = _make_fake_pr(21)

    mock_review = MagicMock()
    mock_review.user.login = "reviewer"
    mock_review.author_association = "COLLABORATOR"
    mock_review.body = "Nit: rename this variable"
    mock_review.path = "src/main.py"
    mock_review.created_at = _OLD
    fake_pr.get_review_comments.return_value = [mock_review]

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [fake_pr])
        harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    data = json.loads(candidate_path(tmp_path, "owner/repo", 21).read_text())
    assert len(data["review_comments"]) == 1
    assert data["review_comments"][0]["path"] == "src/main.py"
    assert data["review_comments"][0]["author_association"] == "COLLABORATOR"


# ------------------------------------------------------------------
# author_prior_prs_in_repo
# ------------------------------------------------------------------

def test_harvest_author_prior_prs_count(tmp_path):
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(5)], search_total=4)
        harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    data = json.loads(candidate_path(tmp_path, "owner/repo", 5).read_text())
    assert data["author_prior_prs_in_repo"] == 3  # 4 total - 1 current


def test_harvest_author_prior_prs_none_on_api_error(tmp_path):
    fake_pr = _make_fake_pr(6)

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        mock_repo = _setup_mock_gh(mock_gh, [fake_pr])
        # Simulate search API failure
        mock_gh.return_value.search_issues.side_effect = Exception("rate limited")
        harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    data = json.loads(candidate_path(tmp_path, "owner/repo", 6).read_text())
    assert data["author_prior_prs_in_repo"] is None


def test_harvest_author_prior_prs_zero_for_first_timer(tmp_path):
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(7)], search_total=1)
        harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    data = json.loads(candidate_path(tmp_path, "owner/repo", 7).read_text())
    assert data["author_prior_prs_in_repo"] == 0  # 1 total - 1 current = 0


# ------------------------------------------------------------------
# closed_at
# ------------------------------------------------------------------

def test_harvest_closed_at_stored(tmp_path):
    fake_pr = _make_fake_pr(8, closed_at=_OLD)

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [fake_pr])
        harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    data = json.loads(candidate_path(tmp_path, "owner/repo", 8).read_text())
    assert data["closed_at"] is not None
