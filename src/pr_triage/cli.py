from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from dotenv import load_dotenv

from pr_triage.github_client import GitHubClient

load_dotenv()

app = typer.Typer(help="PR triage CLI for OSS maintainers.")

_DEFAULT_MAX_TOKENS = 50_000
_FIXTURES_LLM_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "llm"


@app.callback()
def main(
    ctx: typer.Context,
    fake: bool = typer.Option(
        False,
        "--fake",
        help="Replay cached LLM responses from tests/fixtures/llm/ instead of calling the API.",
    ),
) -> None:
    """PR triage CLI for OSS maintainers."""
    ctx.ensure_object(dict)
    ctx.obj["fake"] = fake


@app.command()
def fetch(
    repo: str = typer.Argument(..., help="GitHub repo in owner/repo format"),
    pr_number: int = typer.Argument(..., help="Pull request number"),
) -> None:
    """Fetch a PR and print TriageState as JSON."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        typer.echo("Error: GITHUB_TOKEN is not set.", err=True)
        raise typer.Exit(1)

    if "/" not in repo:
        typer.echo("Error: repo must be in owner/repo format.", err=True)
        raise typer.Exit(1)

    client = GitHubClient(token=token)
    state = client.fetch_pr(repo, pr_number)
    typer.echo(state.model_dump_json(indent=2))


@app.command()
def check(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="GitHub repo in owner/repo format"),
    pr_number: int = typer.Argument(..., help="Pull request number"),
    max_tokens: int = typer.Option(_DEFAULT_MAX_TOKENS, "--max-tokens", help="Token budget cap for this run."),
    output_json: bool = typer.Option(False, "--json", help="Emit full TriageState as JSON instead of human-readable output."),
) -> None:
    """Fetch a PR, run the triage pipeline, and print a guideline-compliance verdict."""
    fake: bool = (ctx.obj or {}).get("fake", False)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        typer.echo("Error: GITHUB_TOKEN is not set.", err=True)
        raise typer.Exit(1)

    if "/" not in repo:
        typer.echo("Error: repo must be in owner/repo format.", err=True)
        raise typer.Exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not fake:
        typer.echo("Error: ANTHROPIC_API_KEY is not set.", err=True)
        raise typer.Exit(1)

    from pr_triage.budget import BudgetExceeded
    from pr_triage.claude_client import ClaudeClient
    from pr_triage.graph.pipeline import run_pipeline
    from pr_triage.rag import RAGIndex

    gh = GitHubClient(token=token)
    typer.echo(f"Fetching PR #{pr_number} from {repo}…", err=True)
    state = gh.fetch_pr(repo, pr_number)

    fake_responses: list[str] | None = None
    if fake:
        fixture_path = _fixture_path(repo, pr_number)
        if not fixture_path.exists():
            typer.echo(
                f"Error: --fake requested but fixture not found: {fixture_path}",
                err=True,
            )
            raise typer.Exit(1)
        fake_responses = json.loads(fixture_path.read_text())

    claude = ClaudeClient(
        api_key=api_key,
        fake=fake,
        fake_responses=fake_responses,
    )
    rag = RAGIndex()

    try:
        result = run_pipeline(state, claude, rag, max_tokens=max_tokens)
    except BudgetExceeded as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(result.model_dump_json(indent=2))
        return

    _print_verdict(result)


@app.command()
def harvest(
    ctx: typer.Context,
    repos: list[str] = typer.Argument(..., help="GitHub repo(s) in owner/repo format."),
    out_dir: Path = typer.Option(Path("data/candidates"), "--out-dir", help="Output directory for candidate JSON files."),
    max_prs: int = typer.Option(200, "--max-prs", help="Max PRs to fetch per repo."),
    states: str = typer.Option("closed", "--states", help="Comma-separated PR states to harvest (closed, open). Defaults to closed-only since open PRs have no decided outcome."),
    re_record: bool = typer.Option(False, "--re-record", help="Re-fetch even if a candidate file already exists."),
    min_age_days: int = typer.Option(14, "--min-age-days", help="Skip PRs closed fewer than this many days ago (settle-time buffer). Set to 0 to disable."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip cost-preflight confirmation."),
) -> None:
    """Harvest PR candidates from GitHub repos into data/candidates/ for golden-set construction."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        typer.echo("Error: GITHUB_TOKEN is not set.", err=True)
        raise typer.Exit(1)

    state_list = [s.strip() for s in states.split(",") if s.strip()]

    from pr_triage.harvest import estimate_harvest_calls, harvest_repo

    for repo in repos:
        if "/" not in repo:
            typer.echo(f"Error: '{repo}' must be in owner/repo format.", err=True)
            raise typer.Exit(1)

        if not yes:
            typer.echo(f"Estimating harvest scope for {repo}…", err=True)
            try:
                est = estimate_harvest_calls(
                    repo, token, out_dir, states=state_list, max_prs=max_prs, re_record=re_record
                )
                typer.echo(
                    f"  {est['estimated_new']} new PRs to fetch, "
                    f"{est['already_cached']} already cached.",
                    err=True,
                )
            except Exception as exc:
                typer.echo(f"  (estimate failed: {exc})", err=True)

            if not typer.confirm(f"Proceed with harvesting {repo}?"):
                typer.echo("Aborted.", err=True)
                raise typer.Exit(0)

        typer.echo(f"Harvesting {repo}…", err=True)
        new_count, skipped = harvest_repo(
            repo, token, out_dir, states=state_list, max_prs=max_prs,
            min_age_days=min_age_days, re_record=re_record, verbose=True,
        )
        typer.echo(f"  Done: {new_count} new, {skipped} skipped.")


@app.command()
def prelabel(
    candidates_dir: Path = typer.Option(Path("data/candidates"), "--candidates-dir", help="Directory of harvested candidate JSON files."),
    out_path: Path = typer.Option(Path("data/pre_labels.jsonl"), "--out", help="Output JSONL path for pre-labels."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Heuristically pre-label all harvested PR candidates."""
    if not candidates_dir.exists():
        typer.echo(f"Error: candidates directory not found: {candidates_dir}", err=True)
        raise typer.Exit(1)

    from pr_triage.prelabel import prelabel_dir

    count = prelabel_dir(candidates_dir, out_path, verbose=verbose)
    typer.echo(f"Pre-labeled {count} candidates → {out_path}")


@app.command(name="golden-build")
def golden_build(
    labels_path: Path = typer.Option(Path("data/golden_labels.jsonl"), "--labels", help="Manual labels JSONL (repo, pr_number, label, notes?)."),
    candidates_dir: Path = typer.Option(Path("data/candidates"), "--candidates-dir"),
    out_dir: Path = typer.Option(Path("tests/fixtures/golden"), "--out-dir"),
    force: bool = typer.Option(False, "--force", help="Write even if class-balance requirements aren't met."),
) -> None:
    """Build the golden test fixture set from manual labels + harvested candidates."""
    from pr_triage.golden import GoldenBuildError, build_golden_set

    try:
        summary = build_golden_set(labels_path, candidates_dir, out_dir, force=force)
    except GoldenBuildError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(
        f"Golden set written to {out_dir}: "
        f"{summary['total']} total "
        f"({summary['approve']} approve, "
        f"{summary['request_changes']} request_changes, "
        f"{summary['reject']} reject)"
    )


@app.command()
def index(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="GitHub repo in owner/repo format"),
    recent_n: int = typer.Option(50, "--recent", help="Number of recent merged PRs to index."),
) -> None:
    """Index a repo's guidelines and recent PR history into ChromaDB."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        typer.echo("Error: GITHUB_TOKEN is not set.", err=True)
        raise typer.Exit(1)

    if "/" not in repo:
        typer.echo("Error: repo must be in owner/repo format.", err=True)
        raise typer.Exit(1)

    from pr_triage.rag import RAGIndex

    typer.echo(f"Fetching repo context for {repo}…", err=True)
    gh = GitHubClient(token=token)
    ctx_data = gh.fetch_repo_context(repo, recent_n=recent_n)

    typer.echo("Indexing into ChromaDB (downloading embedding model on first run)…", err=True)
    rag = RAGIndex()
    n = rag.index_repo(
        repo,
        contributing_md=ctx_data["contributing_md"],
        agents_md=ctx_data["agents_md"],
        merged_prs=ctx_data["merged_prs"],
    )
    typer.echo(f"Indexed {n} chunks for {repo}.")


# ------------------------------------------------------------------
# Output formatting
# ------------------------------------------------------------------

def _print_verdict(state) -> None:
    from pr_triage.state import GuidelinesCriticOutput

    verdict = state.aggregate_verdict
    meta = state.metadata

    typer.echo(f"\nPR #{meta.number} — {meta.title}")
    typer.echo(f"Size: {state.size_classification or 'unknown'}")

    budget = __import__("pr_triage.budget", fromlist=["get_budget"]).get_budget()
    if budget:
        typer.echo(f"Tokens used: {budget.used:,} / {budget.max_tokens:,}")

    typer.echo("")

    if state.size_classification == "trivial":
        typer.echo(f"Verdict: {verdict.decision if verdict else 'approve'} (trivial changeset, critic skipped)")
        return

    guidelines = next(
        (c for c in state.critic_outputs if c.critic_name == "guidelines_critic"),
        None,
    )
    if guidelines and guidelines.details:
        d: GuidelinesCriticOutput = guidelines.details
        typer.echo(f"Guidelines Critic  score: {d.score}/10  verdict: {guidelines.verdict}")
        if d.findings:
            typer.echo("\nFindings:")
            for f in d.findings:
                typer.echo(f"  [{f.severity}] {f.category:<16} — {f.evidence[:120]}")
        else:
            typer.echo("  (no findings)")
        typer.echo(f"\nCitations: {len(d.citations)} of {len(state.rag_chunks)} retrieved chunks used")
        for cid in d.citations:
            typer.echo(f"  · {cid}")

    if verdict:
        typer.echo(f"\nFinal decision: {verdict.decision}  ({verdict.summary})")


def _fixture_path(repo: str, pr_number: int) -> Path:
    safe = repo.replace("/", "__")
    return _FIXTURES_LLM_DIR / f"check_{safe}_pr{pr_number}.json"
