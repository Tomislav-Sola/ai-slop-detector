"""
Tests for GitHubClient.

--fake mode: loads fixture data and drives GitHubClient through mocked PyGithub objects.
Live mode (no --fake): skipped unless GITHUB_TOKEN is set.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from pr_triage.github_client import GitHubClient
from pr_triage.state import TriageState


def _make_mock_repo_and_pr(fixture: dict) -> tuple[MagicMock, MagicMock]:
    pr_data = fixture["pr"]

    mock_label = MagicMock()
    mock_label.name = "bug"

    mock_user = MagicMock()
    mock_user.login = pr_data["user_login"]

    mock_pr = MagicMock()
    mock_pr.number = pr_data["number"]
    mock_pr.title = pr_data["title"]
    mock_pr.body = pr_data["body"]
    mock_pr.user = mock_user
    mock_pr.created_at = datetime.fromisoformat(pr_data["created_at"].replace("Z", "+00:00"))
    mock_pr.updated_at = datetime.fromisoformat(pr_data["updated_at"].replace("Z", "+00:00"))
    mock_pr.base.ref = pr_data["base_ref"]
    mock_pr.head.ref = pr_data["head_ref"]
    mock_pr.additions = pr_data["additions"]
    mock_pr.deletions = pr_data["deletions"]
    mock_pr.changed_files = pr_data["changed_files"]
    mock_pr.labels = []
    mock_pr.draft = pr_data["draft"]
    mock_pr.merged = pr_data["merged"]
    mock_pr.mergeable = pr_data["mergeable"]
    mock_pr.diff_url = pr_data["diff_url"]

    mock_files = [MagicMock(filename=f) for f in fixture["files_changed"]]
    mock_pr.get_files.return_value = mock_files

    # Repo: no CONTRIBUTING / AGENTS files
    from github import GithubException
    mock_repo = MagicMock()
    mock_repo.get_pull.return_value = mock_pr
    mock_repo.get_contents.side_effect = GithubException(404, "not found")

    # No other PRs from this author, a few recent merged PRs
    merged_pr_mocks = []
    for title in fixture["recent_merged_titles"]:
        m = MagicMock()
        m.merged = True
        m.title = title
        m.user.login = "other-user"
        m.number = 999
        merged_pr_mocks.append(m)

    mock_repo.get_pulls.return_value = merged_pr_mocks

    return mock_repo, mock_pr


@pytest.fixture
def fake_github_client(papertriage_pr9):
    """GitHubClient with PyGithub patched using fixture data."""
    mock_repo, _ = _make_mock_repo_and_pr(papertriage_pr9)

    with patch("pr_triage.github_client.Github") as mock_gh_class:
        mock_gh_class.return_value.get_repo.return_value = mock_repo
        with patch("pr_triage.github_client._fetch_diff") as mock_diff:
            mock_diff.return_value = papertriage_pr9["raw_diff"]
            yield GitHubClient(token="fake-token"), papertriage_pr9


def test_fetch_pr_fake_returns_triage_state(fake_github_client):
    client, fixture = fake_github_client
    state = client.fetch_pr(fixture["repo"], fixture["pr_number"])

    assert isinstance(state, TriageState)
    assert state.repo == fixture["repo"]
    assert state.pr_number == fixture["pr_number"]


def test_fetch_pr_fake_metadata(fake_github_client):
    client, fixture = fake_github_client
    state = client.fetch_pr(fixture["repo"], fixture["pr_number"])

    meta = state.metadata
    assert meta.number == fixture["pr"]["number"]
    assert meta.title == fixture["pr"]["title"]
    assert meta.author == fixture["pr"]["user_login"]
    assert meta.additions == fixture["pr"]["additions"]
    assert meta.deletions == fixture["pr"]["deletions"]
    assert meta.base_branch == fixture["pr"]["base_ref"]


def test_fetch_pr_fake_files_changed(fake_github_client):
    client, fixture = fake_github_client
    state = client.fetch_pr(fixture["repo"], fixture["pr_number"])

    assert state.files_changed == fixture["files_changed"]


def test_fetch_pr_fake_raw_diff(fake_github_client):
    client, fixture = fake_github_client
    state = client.fetch_pr(fixture["repo"], fixture["pr_number"])

    assert state.raw_diff == fixture["raw_diff"]


def test_fetch_pr_fake_no_contributing_or_agents(fake_github_client):
    client, fixture = fake_github_client
    state = client.fetch_pr(fixture["repo"], fixture["pr_number"])

    assert state.contributing_md is None
    assert state.agents_md is None


def test_fetch_pr_fake_recent_merged_titles(fake_github_client):
    client, fixture = fake_github_client
    state = client.fetch_pr(fixture["repo"], fixture["pr_number"])

    assert state.recent_merged_titles == fixture["recent_merged_titles"]


def test_fetch_pr_fake_phase2_fields_empty(fake_github_client):
    client, fixture = fake_github_client
    state = client.fetch_pr(fixture["repo"], fixture["pr_number"])

    assert state.critic_outputs == []
    assert state.aggregate_verdict is None
    assert state.rag_chunks == []
    assert state.confidence_score is None


def test_fetch_pr_contributing_md_present(papertriage_pr9):
    mock_repo, _ = _make_mock_repo_and_pr(papertriage_pr9)

    contributing_text = "# Contributing\n\nPlease open an issue first."
    mock_content = MagicMock()
    mock_content.decoded_content = contributing_text.encode()

    from github import GithubException

    def get_contents(name):
        if name == "CONTRIBUTING.md":
            return mock_content
        raise GithubException(404, "not found")

    mock_repo.get_contents.side_effect = get_contents

    with patch("pr_triage.github_client.Github") as mock_gh_class:
        mock_gh_class.return_value.get_repo.return_value = mock_repo
        with patch("pr_triage.github_client._fetch_diff", return_value=None):
            client = GitHubClient(token="fake-token")
            state = client.fetch_pr(papertriage_pr9["repo"], papertriage_pr9["pr_number"])

    assert state.contributing_md == contributing_text
    assert state.agents_md is None


def test_author_prior_prs_excludes_current_pr(papertriage_pr9):
    mock_repo, _ = _make_mock_repo_and_pr(papertriage_pr9)

    author = papertriage_pr9["pr"]["user_login"]
    current = papertriage_pr9["pr_number"]

    def _mock_pr(number, login):
        m = MagicMock()
        m.number = number
        m.user.login = login
        m.merged = True
        m.title = f"PR #{number}"
        return m

    all_prs = [
        _mock_pr(current, author),       # current PR — must be excluded
        _mock_pr(current - 1, author),   # prior PR by same author — counts
        _mock_pr(current - 2, "other"),  # different author — does not count
    ]

    def get_pulls_side_effect(*args, **kwargs):
        if kwargs.get("state") == "all":
            return all_prs
        return [p for p in all_prs if p.merged]

    mock_repo.get_pulls.side_effect = get_pulls_side_effect

    with patch("pr_triage.github_client.Github") as mock_gh_class:
        mock_gh_class.return_value.get_repo.return_value = mock_repo
        with patch("pr_triage.github_client._fetch_diff", return_value=None):
            client = GitHubClient(token="fake-token")
            state = client.fetch_pr(papertriage_pr9["repo"], papertriage_pr9["pr_number"])

    assert state.author_prior_prs == 1
