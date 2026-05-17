from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from dotenv import load_dotenv

from ai_slop_detector.github_client import GitHubClient

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

    from ai_slop_detector.budget import BudgetExceeded
    from ai_slop_detector.claude_client import ClaudeClient
    from ai_slop_detector.graph.pipeline import run_pipeline
    from ai_slop_detector.rag import RAGIndex

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
    out_dir: Path = typer.Option(
        Path("data/candidates_v2"), "--out-dir",
        help="Output directory for candidate JSON files. Default: data/candidates_v2/ (diversity-balanced).",
    ),
    max_prs: int = typer.Option(200, "--max-prs", help="Max PRs to fetch per repo."),
    states: str = typer.Option(
        "closed", "--states",
        help="Comma-separated PR states to harvest (closed, open). Defaults to closed-only.",
    ),
    re_record: bool = typer.Option(False, "--re-record", help="Re-fetch even if a candidate file already exists."),
    min_age_days: int = typer.Option(14, "--min-age-days", help="Skip PRs closed fewer than this many days ago. Set to 0 to disable."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip cost-preflight confirmation."),
    # Diversity options
    max_prs_per_author: int = typer.Option(2, "--max-prs-per-author", help="Max PRs saved per author across all repos."),
    max_prs_per_author_repo_pair: int = typer.Option(2, "--max-prs-per-author-repo-pair", help="Max PRs saved per (author, repo) pair."),
    max_prs_per_repo: int = typer.Option(30, "--max-prs-per-repo", help="Max PRs saved per repo."),
    min_distinct_authors: int = typer.Option(15, "--min-distinct-authors", help="Warn if fewer distinct authors are saved."),
    min_distinct_repos: int = typer.Option(6, "--min-distinct-repos", help="Warn if fewer distinct repos are saved."),
    exclude_authors_csv: str = typer.Option("", "--exclude-authors", help="Comma-separated author logins to exclude entirely."),
    no_exclude_bots: bool = typer.Option(False, "--no-exclude-bots", help="Include bot authors (excluded by default)."),
    seed_from_dir: Path = typer.Option(
        None, "--seed-from-dir",
        help=(
            "Pre-seed diversity counters from an existing candidates dir "
            "(e.g. data/candidates) so no duplicate authors/repos are over-represented."
        ),
    ),
) -> None:
    """Harvest PR candidates from GitHub repos with diversity constraints.

    Results go to data/candidates_v2/ by default (clean separation from the
    unbalanced data/candidates/ set). Diversity limits are applied per-author,
    per-repo, and per (author, repo) pair across all repos in one invocation.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        typer.echo("Error: GITHUB_TOKEN is not set.", err=True)
        raise typer.Exit(1)

    state_list = [s.strip() for s in states.split(",") if s.strip()]
    exclude_authors = [a.strip() for a in exclude_authors_csv.split(",") if a.strip()]

    from ai_slop_detector.harvest import (
        DiversityConfig,
        DiversityTracker,
        estimate_harvest_calls,
        harvest_repo,
    )

    diversity = DiversityConfig(
        max_prs_per_author=max_prs_per_author,
        max_prs_per_author_repo_pair=max_prs_per_author_repo_pair,
        max_prs_per_repo=max_prs_per_repo,
        min_distinct_authors=min_distinct_authors,
        min_distinct_repos=min_distinct_repos,
        exclude_authors=exclude_authors,
        exclude_bot_authors=not no_exclude_bots,
    )

    # Seed tracker once; shared across all repos in this invocation.
    seed_dir = seed_from_dir if seed_from_dir is not None else out_dir
    tracker = DiversityTracker.from_dir(seed_dir)
    if seed_from_dir is not None:
        typer.echo(
            f"Seeded diversity counters from {seed_from_dir}: "
            f"{sum(tracker.author_counts.values())} existing PRs, "
            f"{len(tracker.author_counts)} authors, "
            f"{len(tracker.repo_counts)} repos.",
            err=True,
        )

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
            diversity=diversity, tracker=tracker,
        )
        typer.echo(f"  Done: {new_count} new, {skipped} skipped.")

    # Post-run minimum check (evaluated across ALL repos harvested this invocation)
    for warning in tracker.unmet_minimums(diversity):
        typer.echo(f"Warning: {warning}", err=True)


@app.command()
def prelabel(
    candidates_dir: Path = typer.Option(Path("data/golden_candidates_v2"), "--candidates-dir", help="Directory of harvested candidate JSON files."),
    out_path: Path = typer.Option(Path("data/pre_labels_v2.jsonl"), "--out", help="Output JSONL path for pre-labels."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Heuristically pre-label all harvested PR candidates."""
    if not candidates_dir.exists():
        typer.echo(f"Error: candidates directory not found: {candidates_dir}", err=True)
        raise typer.Exit(1)

    from ai_slop_detector.prelabel import prelabel_dir

    count = prelabel_dir(candidates_dir, out_path, verbose=verbose)
    typer.echo(f"Pre-labeled {count} candidates → {out_path}")


@app.command(name="golden-build")
def golden_build(
    labels_path: Path = typer.Option(Path("data/golden_labels.jsonl"), "--labels", help="Manual labels JSONL (repo, pr_number, is_slop, notes?)."),
    candidates_dirs_csv: str = typer.Option(
        "data/golden_candidates_v2,data/candidates",
        "--candidates-dirs",
        help=(
            "Comma-separated list of candidate directories. Searched in order; "
            "the first match wins. Put the newest harvest first so later-added "
            "fields (e.g. author_association) are preserved."
        ),
    ),
    out_dir: Path = typer.Option(Path("tests/fixtures/golden"), "--out-dir"),
    force: bool = typer.Option(False, "--force", help="Write even if class-balance requirements aren't met."),
) -> None:
    """Build the golden test fixture set from manual labels + harvested candidates."""
    from ai_slop_detector.golden import GoldenBuildError, build_golden_set

    candidates_dirs = [Path(p.strip()) for p in candidates_dirs_csv.split(",") if p.strip()]

    try:
        summary = build_golden_set(labels_path, candidates_dirs, out_dir, force=force)
    except GoldenBuildError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(
        f"Golden set written to {out_dir}: "
        f"{summary['total']} total — "
        f"is_slop=True: {summary['is_slop']}, is_slop=False: {summary['not_slop']}"
    )


@app.command()
def label(
    pre_labels: Path = typer.Option(Path("data/pre_labels_v2.jsonl"), "--pre-labels"),
    candidates_dir: Path = typer.Option(Path("data/golden_candidates_v2"), "--candidates-dir"),
    out: Path = typer.Option(Path("data/golden_labels.jsonl"), "--out"),
    queue: str = typer.Option(
        "slop-first",
        "--queue",
        help=(
            "Queue ordering. Options: 'slop-first' (default: slop by signal count desc, "
            "then rejected_quality, accepted, unclear), 'confidence-asc' (hardest first), "
            "'label=<name>' (single class only, e.g. --queue label=slop)."
        ),
    ),
    skip_maintainer_cleanups: bool = typer.Option(
        False,
        "--skip-maintainer-cleanups",
        help=(
            "Auto-skip PRs that look like maintainer cleanups "
            "(merged=false, prior_prs>=50, no comments). Written as 'skip' to the output."
        ),
    ),
) -> None:
    """Launch the Streamlit manual labeling tool."""
    import subprocess
    import sys

    if not pre_labels.exists():
        typer.echo(f"Error: {pre_labels} not found. Run `ai-slop-detector prelabel` first.", err=True)
        raise typer.Exit(1)

    app_path = Path(__file__).parent / "labeler_app.py"
    env = os.environ.copy()
    env["LABELER_PRE_LABELS"] = str(pre_labels.resolve())
    env["LABELER_CANDIDATES_DIR"] = str(candidates_dir.resolve())
    env["LABELER_OUT"] = str(out.resolve())
    env["LABELER_QUEUE_MODE"] = queue
    env["LABELER_SKIP_MAINTAINER"] = "1" if skip_maintainer_cleanups else "0"

    try:
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", str(app_path)],
            env=env,
            check=True,
        )
    except FileNotFoundError:
        typer.echo("Error: streamlit not found. Install with: pip install -e '.[eval]'", err=True)
        raise typer.Exit(1)


@app.command()
def eval(
    golden_dir: Path = typer.Option(Path("tests/fixtures/golden"), "--golden-dir"),
    out_dir: Path = typer.Option(Path("outputs/eval_runs"), "--out-dir"),
    ablation: str = typer.Option("", "--ablation", help="Critic name to exclude (e.g. slop_signals_critic)."),
    limit: int = typer.Option(0, "--limit", help="Max entries to evaluate (0 = all)."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    model: str = typer.Option("sonnet", "--model", help="Model for critics: 'sonnet' (default, production quality) or 'haiku' (cheap iteration)."),
) -> None:
    """Run the eval harness against the golden test set and print metrics."""
    from ai_slop_detector.claude_client import MODEL_HAIKU, MODEL_SONNET
    from ai_slop_detector.eval import run_eval

    critic_model = MODEL_SONNET if model.lower().startswith("sonnet") else MODEL_HAIKU
    typer.echo(f"Running eval on {golden_dir} (critics: {critic_model})…", err=True)
    try:
        run = run_eval(
            golden_dir=golden_dir,
            out_dir=out_dir,
            ablation_critic=ablation or None,
            limit=limit or None,
            verbose=verbose,
            critic_model=critic_model,
        )
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    m = run["metrics"]
    cost = run.get("cost_usd", 0)
    typer.echo(
        f"\nSlop precision: {m['slop_precision']:.3f}   recall: {m['slop_recall']:.3f}   "
        f"F1: {m['slop_f1']:.3f}   |   accuracy: {m['accuracy']:.1%} ({m['n_correct']}/{m['n_total']})   "
        f"|   cost: ${cost:.4f}"
    )
    typer.echo("")
    typer.echo("Binary confusion matrix (rows=true is_slop, cols=predicted):")
    decisions = ["approve", "reject"]
    typer.echo(f"{'':22}" + "".join(f"{d:>12}" for d in decisions))
    for true_cls in decisions:
        row = f"{'not-slop' if true_cls == 'approve' else 'slop':<22}"
        row += "".join(f"{m['confusion_matrix'][true_cls][pred]:>12}" for pred in decisions)
        typer.echo(row)
    typer.echo("")

    # Find the most recent output file
    runs = sorted(out_dir.glob("*.json"), reverse=True)
    if runs:
        typer.echo(f"Results saved to {runs[0]}")


@app.command()
def view(
    run_file: Path = typer.Option(None, "--run", help="Specific eval run JSON (default: latest in outputs/eval_runs/)."),
) -> None:
    """Open the Streamlit eval viewer dashboard."""
    import subprocess
    import sys

    out_dir = Path("outputs/eval_runs")
    if run_file is None:
        runs = sorted(out_dir.glob("*.json"), reverse=True)
        if not runs:
            typer.echo("Error: no eval runs found. Run `ai-slop-detector eval` first.", err=True)
            raise typer.Exit(1)
        run_file = runs[0]

    if not run_file.exists():
        typer.echo(f"Error: run file not found: {run_file}", err=True)
        raise typer.Exit(1)

    app_path = Path(__file__).parent / "eval_viewer_app.py"
    env = os.environ.copy()
    env["EVAL_RUN_FILE"] = str(run_file.resolve())

    try:
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", str(app_path)],
            env=env,
            check=True,
        )
    except FileNotFoundError:
        typer.echo("Error: streamlit not found. Install with: pip install -e '.[eval]'", err=True)
        raise typer.Exit(1)


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

    from ai_slop_detector.rag import RAGIndex

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
    from ai_slop_detector.state import GuidelinesCriticOutput

    verdict = state.aggregate_verdict
    meta = state.metadata

    typer.echo(f"\nPR #{meta.number} — {meta.title}")
    typer.echo(f"Size: {state.size_classification or 'unknown'}")

    budget = __import__("ai_slop_detector.budget", fromlist=["get_budget"]).get_budget()
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
