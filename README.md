# pr-triage

A CLI tool and GitHub Action that triages pull requests using a multi-agent LangGraph pipeline with RAG. Built to help OSS maintainers handle the surge of low-quality, AI-generated PRs.

This is portfolio project #2, built in public.

## What this is

`pr-triage check <owner/repo> <pr_number>` fetches a PR from GitHub, retrieves relevant guideline context from a per-repo ChromaDB index, classifies the PR size, and runs a guidelines-compliance critic (Claude Sonnet) that returns a structured score, findings, and citations. The result prints as human-readable output or as full `TriageState` JSON for downstream consumers (e.g. a GitHub Action comment).

## Phase 2 deliverables

- `ClaudeClient` — real Anthropic SDK calls, model routing (Sonnet for critics, Haiku for classification), tenacity retry on 429 / 5xx / connection errors, per-run token budget cap
- `RAGIndex` — ChromaDB persistent store at `data/chroma/`, sentence-transformers `all-MiniLM-L6-v2` embeddings, 800-1000 char paragraph chunks with heading prefix, per-repo indexing
- LangGraph pipeline — `ingest_pr → classify_size → retrieve_context → guidelines_critic → emit_verdict`; trivial changesets skip the critic
- CLI — `pr-triage check`, `pr-triage index`, global `--fake` flag for offline replay from `tests/fixtures/llm/`
- `--json` flag on `check` emits the full `TriageState` (consumed by Phase 4 GitHub Action)
- 91 tests (87% coverage; remaining gaps are live-API paths — GitHub, Anthropic, ChromaDB)

## Phase 1 deliverables

- GitHub PR ingestion via PyGithub
- `TriageState` Pydantic model
- `ClaudeClient` gateway (stub in Phase 1, real in Phase 2)
- Per-run token budget cap via `ContextVar`
- CLI: `pr-triage fetch <owner/repo> <pr_number>` prints full JSON
- Pytest suite with `--fake` mode

## Non-goals

- Not a general-purpose code review tool
- No GitHub Action packaging yet (Phase 4)

## Current phase status

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | Done | Repo skeleton, GitHub ingestion, CLI |
| 2 | Done | LangGraph critic pipeline, RAG, guidelines critic |
| 3 | Planned | Additional critics, eval harness |
| 4 | Planned | GitHub Action packaging |

## Usage

```bash
# Install
pip install -e ".[dev]"

# Credentials — shell environment variables take priority.
# .env (if present) provides fallback values for variables not set in the shell.
cp .env.example .env   # fill in your tokens, never commit .env

# Index a repo's guidelines and recent PR history into ChromaDB
# (downloads all-MiniLM-L6-v2 on first run, ~90 MB)
pr-triage index owner/repo

# Run the triage critic on a PR
pr-triage check owner/repo 42

# JSON output — full TriageState, useful for scripting or Phase 4
pr-triage check owner/repo 42 --json

# Dry-run without API calls — replays cached LLM responses
pr-triage --fake check owner/repo 42

# Raise the token budget cap (default 50 000)
pr-triage check owner/repo 42 --max-tokens 100000

# Phase 1: just fetch and inspect the raw state
pr-triage fetch owner/repo 42
```

## Development

```bash
pip install -e ".[dev]"
pytest                    # unit + fake-mode tests, no live credentials needed
pytest --fake             # same, explicit flag
pytest --cov=src/pr_triage --cov-report=term-missing
```

`pytest-cov` is included in the `dev` extra.

## Project structure

```
src/pr_triage/
├── cli.py              # Typer entry point: fetch, check, index; global --fake flag
├── github_client.py    # PyGithub wrapper; fetch_pr, fetch_repo_context
├── claude_client.py    # Claude API gateway — real SDK + fake replay mode
├── state.py            # TriageState and Pydantic models
├── budget.py           # Token budget ContextVar
├── rag.py              # ChromaDB index + sentence-transformers retrieval
└── graph/
    ├── nodes.py        # LangGraph node functions
    └── pipeline.py     # StateGraph assembly, run_pipeline(), budget pre-check
tests/
└── fixtures/
    ├── papertriage_pr9.json            # Recorded GitHub PR #9
    └── llm/
        └── check_<owner>__<repo>_pr<N>.json  # Recorded LLM response sequences
```
