"""GitHub Action entry point for ai-slop-detector.

Reads the pull_request event from $GITHUB_EVENT_PATH, indexes the target
repo into ChromaDB, runs the binary slop-detection pipeline, and posts an
idempotent comment when the PR looks like AI slop (or, with verbose=true,
also when it looks clean).

Fail-open contract: any uncaught error logs to stderr and posts an honest
"couldn't analyse" note. The Action never claims "looks clean" on a
failure path — silence beats a misleading reassurance.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.request
from pathlib import Path
from typing import Any

from ai_slop_detector.claude_client import ClaudeClient
from ai_slop_detector.github_client import GitHubClient
from ai_slop_detector.graph.pipeline import run_pipeline
from ai_slop_detector.rag import RAGIndex
from ai_slop_detector.state import AggregateResult, TriageState

COMMENT_MARKER = "<!-- ai-slop-detector-marker -->"
GITHUB_API = "https://api.github.com"
REPO_URL = "https://github.com/Tomislav-Sola/ai-slop-detector"
SCORE_DOCS_ANCHOR = f"{REPO_URL}#how-scoring-works"


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> int:
    """Run the Action. Always returns 0 (fail-open)."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        _log("GITHUB_EVENT_PATH not set; nothing to do.")
        return 0

    try:
        event = json.loads(Path(event_path).read_text())
    except Exception as exc:
        _log(f"could not parse event payload: {exc!r}")
        return 0

    if "pull_request" not in event or "repository" not in event:
        _log("event payload is not a pull_request; skipping.")
        return 0

    repo_full = event["repository"]["full_name"]
    pr_number = int(event["pull_request"]["number"])
    _log(f"analysing {repo_full}#{pr_number}")

    try:
        _run(repo_full, pr_number)
    except _MissingInput as exc:
        # Configuration error — log loudly but do not post (no key, no auth).
        sys.stderr.write(f"ai-slop-detector: {exc}\n")
    except Exception as exc:
        sys.stderr.write(f"ai-slop-detector: unexpected error: {exc!r}\n")
        traceback.print_exc(file=sys.stderr)
        _try_post_failure_comment(repo_full, pr_number, exc)
    return 0


def _run(repo_full: str, pr_number: int) -> None:
    """Core pipeline invocation. Exceptions propagate to main()'s fail-open."""
    verbose = _bool_input("VERBOSE", default=False)
    max_tokens = _int_input("MAX_TOKENS", default=50_000)
    anthropic_key = _required_input("ANTHROPIC_API_KEY")
    github_token = (
        os.environ.get("INPUT_GITHUB_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    )

    os.environ["ANTHROPIC_API_KEY"] = anthropic_key
    if github_token:
        os.environ["GITHUB_TOKEN"] = github_token

    gh = GitHubClient(token=github_token)
    state = gh.fetch_pr(repo_full, pr_number)

    rag = RAGIndex()
    try:
        ctx_data = gh.fetch_repo_context(repo_full, recent_n=50)
        chunks = rag.index_repo(
            repo_full,
            contributing_md=ctx_data["contributing_md"],
            agents_md=ctx_data["agents_md"],
            merged_prs=ctx_data["merged_prs"],
        )
        _log(f"indexed {chunks} chunks for {repo_full}")
    except Exception as exc:
        # Indexing isn't load-bearing — RAG retrieval degrades gracefully.
        _log(f"indexing failed ({exc!r}); pipeline continues without RAG context")

    claude = ClaudeClient(api_key=anthropic_key)
    result = run_pipeline(state, claude, rag, max_tokens=max_tokens)

    agg = result.aggregate_result
    if agg is None:
        _log("aggregator produced no result; not commenting")
        return

    is_slop = agg.decision == "reject"
    if is_slop:
        body = _build_slop_comment(result, agg)
        _upsert_comment(repo_full, pr_number, github_token, body)
        _log("posted slop comment")
    elif verbose:
        body = _build_clean_comment(result, agg)
        _upsert_comment(repo_full, pr_number, github_token, body)
        _log("posted clean (verbose) comment")
    else:
        _log("not slop and verbose=false; staying silent")


# ------------------------------------------------------------------
# Comment bodies
# ------------------------------------------------------------------

def _build_slop_comment(state: TriageState, agg: AggregateResult) -> str:
    arch = agg.per_critic_scores.get("architecture_critic", "—")
    slop = agg.per_critic_scores.get("slop_signals_critic", "—")

    factors = [f for f in agg.deciding_factors[:3] if f]
    if factors:
        factors_md = "\n".join(f"- {f}" for f in factors)
    else:
        factors_md = "_no specific findings surfaced_"

    return f"""{COMMENT_MARKER}
## 🤖 ai-slop-detector: this PR looks like AI slop

{agg.summary}

**Critic scores**
- `architecture_critic`: **{arch}**/10
- `slop_signals_critic`: **{slop}**/10

**Top deciding factors**
{factors_md}

[How the score is computed →]({SCORE_DOCS_ANCHOR})

---

_This is an automated first-look signal, not a review. The model can be wrong
and is intentionally biased toward catching slop, so some false positives land
on legitimate PRs whose diffs carry slop-adjacent patterns. Make your own call.
Methodology and golden-set eval at [{REPO_URL}]({REPO_URL})._
"""


def _build_clean_comment(state: TriageState, agg: AggregateResult) -> str:
    return f"""{COMMENT_MARKER}
## ✅ ai-slop-detector: looks clean

{agg.summary}

_Automated first-look signal. Methodology at [{REPO_URL}]({REPO_URL})._
"""


def _build_failure_comment(exc: Exception) -> str:
    return f"""{COMMENT_MARKER}
## ⚠️ ai-slop-detector: couldn't analyse this PR

The pipeline errored before producing a verdict (`{type(exc).__name__}`).
No conclusions either way — maintainer review as normal.

_Methodology at [{REPO_URL}]({REPO_URL})._
"""


# ------------------------------------------------------------------
# Idempotent comment upsert (find by marker, then PATCH or POST)
# ------------------------------------------------------------------

def _upsert_comment(repo: str, pr_number: int, token: str, body: str) -> None:
    existing = _find_marker_comment(repo, pr_number, token)
    if existing is not None:
        _patch_comment(repo, existing, token, body)
    else:
        _post_comment(repo, pr_number, token, body)


def _find_marker_comment(repo: str, pr_number: int, token: str) -> int | None:
    base = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    page = 1
    while True:
        comments = _gh_request("GET", f"{base}?per_page=100&page={page}", token)
        if not comments:
            return None
        for c in comments:
            if COMMENT_MARKER in (c.get("body") or ""):
                return int(c["id"])
        if len(comments) < 100:
            return None
        page += 1


def _post_comment(repo: str, pr_number: int, token: str, body: str) -> None:
    url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    _gh_request("POST", url, token, payload={"body": body})


def _patch_comment(repo: str, comment_id: int, token: str, body: str) -> None:
    url = f"{GITHUB_API}/repos/{repo}/issues/comments/{comment_id}"
    _gh_request("PATCH", url, token, payload={"body": body})


def _gh_request(method: str, url: str, token: str, *, payload: dict | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-slop-detector",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body_bytes = resp.read()
        if not body_bytes:
            return None
        return json.loads(body_bytes)


# ------------------------------------------------------------------
# Input parsing + fail-open helpers
# ------------------------------------------------------------------

class _MissingInput(RuntimeError):
    pass


def _required_input(name: str) -> str:
    val = os.environ.get(f"INPUT_{name}") or os.environ.get(name)
    if not val:
        raise _MissingInput(f"required input '{name.lower()}' is empty")
    return val


def _bool_input(name: str, *, default: bool) -> bool:
    val = os.environ.get(f"INPUT_{name}", "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _int_input(name: str, *, default: int) -> int:
    val = os.environ.get(f"INPUT_{name}", "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        _log(f"invalid int for INPUT_{name}: {val!r}; using default {default}")
        return default


def _log(msg: str) -> None:
    sys.stderr.write(f"ai-slop-detector: {msg}\n")


def _try_post_failure_comment(repo: str, pr_number: int, exc: Exception) -> None:
    """Best-effort: post an honest 'couldn't analyse' note. Swallow all errors."""
    try:
        token = (
            os.environ.get("INPUT_GITHUB_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or ""
        )
        if not token:
            return
        _upsert_comment(repo, pr_number, token, _build_failure_comment(exc))
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
