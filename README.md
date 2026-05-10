# pr-triage

A CLI tool and GitHub Action that triages pull requests using a multi-agent LangGraph pipeline with RAG. Built to help OSS maintainers handle the surge of low-quality, AI-generated PRs.

This is portfolio project #2, built in public.

## What this is

`pr-triage fetch <owner/repo> <pr_number>` fetches a pull request from GitHub and prints a structured `TriageState` JSON — PR metadata, diff, files changed, author history, and repo contribution guidelines. Later phases will run critic agents over that state and produce a triage verdict.

## MVP scope (Phase 1)

- GitHub PR ingestion via PyGithub
- `TriageState` Pydantic model capturing all data needed by later agent phases
- `ClaudeClient` gateway stub (no LLM calls yet)
- Per-run token budget cap via `ContextVar`
- CLI: `pr-triage fetch <owner/repo> <pr_number>` prints JSON to stdout
- Pytest suite with `--fake` mode using recorded fixture data

## Non-goals

- No LLM calls in Phase 1
- No GitHub Action packaging yet
- Not a general-purpose code review tool
- Not optimized for throughput or latency

## Current phase status

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | In progress | Repo skeleton, GitHub ingestion, CLI |
| 2 | Planned | Multi-agent LangGraph critic pipeline |
| 3 | Planned | RAG context retrieval |
| 4 | Planned | GitHub Action packaging |

## Usage

```bash
# Install
pip install -e ".[dev]"

# Set credentials
export GITHUB_TOKEN=ghp_...
export ANTHROPIC_API_KEY=sk-ant-...  # not used until Phase 2

# Fetch a PR
pr-triage fetch owner/repo 42
```

## Development

```bash
pip install -e ".[dev]"
pytest                    # live mode (needs GITHUB_TOKEN)
pytest --fake             # use recorded fixture data, no credentials needed
```

## Project structure

```
src/pr_triage/
├── cli.py           # Typer entry point
├── github_client.py # PyGithub wrapper
├── claude_client.py # Claude API gateway (stub in Phase 1)
├── state.py         # TriageState Pydantic model
└── budget.py        # Token budget ContextVar
tests/
└── fixtures/        # Recorded GitHub API responses for --fake mode
```
