from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pr_triage.harvest import (
    DiversityConfig,
    DiversityTracker,
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

def _make_fake_pr(number: int, title: str = "PR title", *, closed_at=None, author: str = "alice") -> MagicMock:
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.body = None
    pr.user.login = author
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
    # raw_data used for settle-time check and author_association
    pr.raw_data = {
        "closed_at": _OLD.isoformat() if closed_at is None else (closed_at.isoformat() if closed_at else None),
        "author_association": "CONTRIBUTOR",
    }
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
    assert data["author_association"] == "CONTRIBUTOR"


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


# ------------------------------------------------------------------
# DiversityTracker.from_dir
# ------------------------------------------------------------------

def test_diversity_tracker_from_empty_dir(tmp_path):
    tracker = DiversityTracker.from_dir(tmp_path)
    assert len(tracker.author_counts) == 0
    assert len(tracker.repo_counts) == 0


def test_diversity_tracker_from_nonexistent_dir(tmp_path):
    tracker = DiversityTracker.from_dir(tmp_path / "does_not_exist")
    assert len(tracker.author_counts) == 0


def test_diversity_tracker_from_dir_counts(tmp_path):
    for pr_num, author in [(1, "alice"), (2, "alice"), (3, "bob")]:
        f = candidate_path(tmp_path, "owner/repo", pr_num)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"repo": "owner/repo", "author": author, "pr_number": pr_num}))

    tracker = DiversityTracker.from_dir(tmp_path)
    assert tracker.author_counts["alice"] == 2
    assert tracker.author_counts["bob"] == 1
    assert tracker.repo_counts["owner/repo"] == 3
    assert tracker.author_repo_counts[("alice", "owner/repo")] == 2
    assert tracker.author_repo_counts[("bob", "owner/repo")] == 1


def test_diversity_tracker_from_dir_skips_malformed(tmp_path):
    (tmp_path / "bad.json").write_text("not json at all")
    # Should not raise; malformed file is silently skipped
    tracker = DiversityTracker.from_dir(tmp_path)
    assert len(tracker.author_counts) == 0


# ------------------------------------------------------------------
# DiversityTracker.check
# ------------------------------------------------------------------

def test_diversity_check_passes_clean_pr():
    config = DiversityConfig(max_prs_per_author=2, max_prs_per_repo=10, max_prs_per_author_repo_pair=2)
    tracker = DiversityTracker()
    assert tracker.check("alice", "owner/repo", config) is None


def test_diversity_check_max_prs_per_author():
    config = DiversityConfig(max_prs_per_author=2)
    tracker = DiversityTracker()
    tracker.author_counts["alice"] = 2
    reason = tracker.check("alice", "owner/repo", config)
    assert reason is not None
    assert "max_prs_per_author" in reason
    assert "alice" in reason


def test_diversity_check_max_prs_per_repo():
    config = DiversityConfig(max_prs_per_repo=5)
    tracker = DiversityTracker()
    tracker.repo_counts["owner/repo"] = 5
    reason = tracker.check("alice", "owner/repo", config)
    assert reason is not None
    assert "max_prs_per_repo" in reason


def test_diversity_check_max_prs_per_author_repo_pair():
    config = DiversityConfig(max_prs_per_author_repo_pair=2)
    tracker = DiversityTracker()
    tracker.author_repo_counts[("alice", "owner/repo")] = 2
    reason = tracker.check("alice", "owner/repo", config)
    assert reason is not None
    assert "max_prs_per_author_repo_pair" in reason


def test_diversity_check_exclude_authors():
    config = DiversityConfig(exclude_authors=["spammer"])
    tracker = DiversityTracker()
    reason = tracker.check("spammer", "owner/repo", config)
    assert reason is not None
    assert "excluded_author" in reason


def test_diversity_check_exclude_bot_authors():
    config = DiversityConfig(exclude_bot_authors=True)
    tracker = DiversityTracker()
    reason = tracker.check("ci-bot[bot]", "owner/repo", config)
    assert reason is not None
    assert "bot_author" in reason


def test_diversity_check_bots_allowed_when_flag_off():
    config = DiversityConfig(exclude_bot_authors=False)
    tracker = DiversityTracker()
    assert tracker.check("ci-bot[bot]", "owner/repo", config) is None


# ------------------------------------------------------------------
# DiversityTracker.unmet_minimums
# ------------------------------------------------------------------

def test_diversity_unmet_minimums_both_met():
    config = DiversityConfig(min_distinct_authors=2, min_distinct_repos=1)
    tracker = DiversityTracker()
    tracker.author_counts.update(["alice", "bob"])
    tracker.repo_counts["owner/repo"] = 2
    assert tracker.unmet_minimums(config) == []


def test_diversity_unmet_minimums_authors_short():
    config = DiversityConfig(min_distinct_authors=5, min_distinct_repos=1)
    tracker = DiversityTracker()
    tracker.author_counts["alice"] = 1
    tracker.repo_counts["owner/repo"] = 1
    warnings = tracker.unmet_minimums(config)
    assert len(warnings) == 1
    assert "min_distinct_authors" in warnings[0]


def test_diversity_unmet_minimums_repos_short():
    config = DiversityConfig(min_distinct_authors=1, min_distinct_repos=3)
    tracker = DiversityTracker()
    tracker.author_counts["alice"] = 1
    tracker.repo_counts["owner/repo"] = 1
    warnings = tracker.unmet_minimums(config)
    assert len(warnings) == 1
    assert "min_distinct_repos" in warnings[0]


# ------------------------------------------------------------------
# harvest_repo — diversity integration
# ------------------------------------------------------------------

def test_diversity_harvest_author_cap(tmp_path):
    config = DiversityConfig(max_prs_per_author=2)
    tracker = DiversityTracker()
    tracker.author_counts["alice"] = 2  # already at cap

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(1, author="alice")])
        new_count, skipped = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert new_count == 0
    assert not candidate_path(tmp_path, "owner/repo", 1).exists()


def test_diversity_harvest_repo_cap(tmp_path):
    config = DiversityConfig(max_prs_per_repo=1)
    tracker = DiversityTracker()
    tracker.repo_counts["owner/repo"] = 1  # already at cap

    prs = [_make_fake_pr(i, author=f"user{i}") for i in range(1, 4)]
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, prs)
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert new_count == 0


def test_diversity_harvest_author_repo_pair_cap(tmp_path):
    config = DiversityConfig(max_prs_per_author_repo_pair=1)
    tracker = DiversityTracker()
    tracker.author_repo_counts[("alice", "owner/repo")] = 1

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(10, author="alice")])
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert new_count == 0


def test_diversity_harvest_different_authors_all_pass(tmp_path):
    config = DiversityConfig(max_prs_per_author=1)
    tracker = DiversityTracker()

    prs = [_make_fake_pr(i, author=f"user{i}") for i in range(1, 4)]
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, prs)
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert new_count == 3
    assert tracker.author_counts["user1"] == 1
    assert tracker.author_counts["user2"] == 1
    assert tracker.author_counts["user3"] == 1


def test_diversity_harvest_tracker_updated_after_save(tmp_path):
    config = DiversityConfig(max_prs_per_author=2)
    tracker = DiversityTracker()

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(1, author="alice")])
        harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert tracker.author_counts["alice"] == 1
    assert tracker.repo_counts["owner/repo"] == 1
    assert tracker.author_repo_counts[("alice", "owner/repo")] == 1


def test_diversity_harvest_existing_files_count_against_limits(tmp_path):
    # Pre-write 2 files for alice — simulates a previous run
    for pr_num in [1, 2]:
        f = candidate_path(tmp_path, "owner/repo", pr_num)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"repo": "owner/repo", "author": "alice", "pr_number": pr_num}))

    config = DiversityConfig(max_prs_per_author=2)
    tracker = DiversityTracker.from_dir(tmp_path)
    assert tracker.author_counts["alice"] == 2

    # PR #3 is new but alice is already at the cap
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(3, author="alice")])
        new_count, skipped = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert new_count == 0
    assert skipped == 1
    assert not candidate_path(tmp_path, "owner/repo", 3).exists()


def test_diversity_harvest_no_config_unchanged_behavior(tmp_path):
    # Without diversity args, harvest_repo behaves exactly as before
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(99, author="alice")], search_total=1)
        new_count, _ = harvest_repo("owner/repo", "token", tmp_path, states=["closed"])

    assert new_count == 1
    assert candidate_path(tmp_path, "owner/repo", 99).exists()


def test_diversity_harvest_exclude_author(tmp_path):
    config = DiversityConfig(exclude_authors=["spammer"])
    tracker = DiversityTracker()

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(5, author="spammer")])
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert new_count == 0
    assert not candidate_path(tmp_path, "owner/repo", 5).exists()


def test_diversity_harvest_exclude_bot_author(tmp_path):
    config = DiversityConfig(exclude_bot_authors=True)
    tracker = DiversityTracker()

    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, [_make_fake_pr(6, author="renovate[bot]")])
        new_count, _ = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert new_count == 0


def test_diversity_harvest_mixed_authors_caps_correctly(tmp_path):
    # alice gets 1 PR (cap=1), bob gets 2 PRs (cap=2)
    config = DiversityConfig(max_prs_per_author=1, max_prs_per_author_repo_pair=2, max_prs_per_repo=10)
    tracker = DiversityTracker()

    prs = [
        _make_fake_pr(1, author="alice"),
        _make_fake_pr(2, author="alice"),  # should be capped
        _make_fake_pr(3, author="bob"),
    ]
    with patch("pr_triage.harvest.Github") as mock_gh, \
         patch("pr_triage.harvest._fetch_diff", return_value=None):
        _setup_mock_gh(mock_gh, prs)
        new_count, skipped = harvest_repo(
            "owner/repo", "token", tmp_path, states=["closed"],
            diversity=config, tracker=tracker,
        )

    assert new_count == 2  # alice#1 + bob#3
    assert skipped == 1    # alice#2 capped
    assert candidate_path(tmp_path, "owner/repo", 1).exists()
    assert not candidate_path(tmp_path, "owner/repo", 2).exists()
    assert candidate_path(tmp_path, "owner/repo", 3).exists()
