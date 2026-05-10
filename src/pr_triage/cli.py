from __future__ import annotations

import os
import sys

import typer
from dotenv import load_dotenv

from pr_triage.github_client import GitHubClient

load_dotenv()

app = typer.Typer(help="PR triage CLI for OSS maintainers.")


@app.callback()
def main() -> None:
    """PR triage CLI for OSS maintainers."""


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
