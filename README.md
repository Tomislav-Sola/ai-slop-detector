# pr-triage

A CLI tool and GitHub Action that triages pull requests using a multi-critic LangGraph pipeline with RAG. Built to help OSS maintainers handle the surge of low-quality, AI-generated PRs.

This is portfolio project #2, built in public.

## What this is

`pr-triage check <owner/repo> <pr_number>` fetches a PR from GitHub, retrieves relevant guideline context from a per-repo ChromaDB index, classifies the PR size, and runs three critics in parallel (guidelines compliance, architecture consistency, slop signals). A deterministic aggregator applies a veto rule and emits a structured verdict with score, findings, and citations.

## Phase 3 deliverables

- **53-entry golden set** — 20 accepted, 20 rejected (quality), 13 slop, spanning 8 repos (ruff, pydantic, poetry, godot, Home Assistant, ghostty, curl, tldraw)
- **Three critics in parallel** — `guidelines_critic`, `architecture_critic`, `slop_signals_critic`; wired as a LangGraph parallel fan-out/fan-in with `operator.add` reducer
- **Deterministic aggregator** with veto rule: any critic score ≤ 4 → `request_changes`; majority otherwise
- **`pr-triage eval`** — runs the full pipeline against the golden set, emits a JSON results file with per-entry verdicts, accuracy, and cost
- **`pr-triage view`** — Streamlit eval viewer for browsing results (score heatmaps, findings, side-by-side diff)
- **`pr-triage label`** — Streamlit manual labeling tool for building the golden set
- **`pr-triage prelabel` / `harvest`** — automated harvest and heuristic pre-labeling pipeline
- **`pr-triage golden-build`** — CLI builder for golden fixture files
- **Per-critic model split** — `guidelines_critic` and `architecture_critic` use Sonnet; `slop_signals_critic` uses Haiku by default; `eval` defaults to all-Haiku with `--model sonnet` override
- **Dollar cost guardrail** — `MAX_EVAL_COST_USD` in `.env` stops the eval loop before it exceeds budget
- **Cost tracking** — `ClaudeClient.total_cost_usd` property; printed after every eval run

**Eval results (2026-05-15, 53-entry golden set):**

| Run | Model | RAG | Accuracy | Cost/run |
|-----|-------|-----|----------|----------|
| Baseline | Sonnet | No | 50.9% (27/53) | ~$2–4 |
| Mixed | Haiku critics | 8 repos | 43.4% (23/53) | $0.72 |

The gap is mainly Haiku's near-zero slop recall (0.077 vs 0.385 for Sonnet). A clean mixed-model run (Sonnet for all three critics) is the next step before tagging v0.3.0.

## Phase 2 deliverables

- `ClaudeClient` — real Anthropic SDK calls, model routing (Sonnet for critics, Haiku for classification), tenacity retry on 429 / 5xx / connection errors, per-run token budget cap
- `RAGIndex` — ChromaDB persistent store at `data/chroma/`, sentence-transformers `all-MiniLM-L6-v2` embeddings, 800-1000 char paragraph chunks with heading prefix, per-repo indexing
- LangGraph pipeline — `ingest_pr → classify_size → retrieve_context → guidelines_critic → emit_verdict`
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
| 3 | Done | Multi-critic pipeline, golden set, eval harness |
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

# Run eval against the golden set (Haiku, cheap)
pr-triage eval

# Run eval with Sonnet critics (better recall, ~$3/run)
pr-triage eval --model sonnet

# Browse eval results in Streamlit
pr-triage view outputs/eval_runs/<run_id>.json

# Harvest candidate PRs from a repo
pr-triage harvest owner/repo --max-prs 50

# Heuristic pre-labeling of harvested candidates
pr-triage prelabel outputs/candidates/<repo>.jsonl

# Build a golden fixture from a labeled candidate
pr-triage golden-build outputs/candidates/<repo>.jsonl --pr <number>

# Open the manual labeling UI
pr-triage label outputs/candidates/<repo>.jsonl

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
├── cli.py              # Typer entry point: fetch, check, index, eval, view, label, ...
├── github_client.py    # PyGithub wrapper; fetch_pr, fetch_repo_context
├── claude_client.py    # Claude API gateway — real SDK + fake replay mode + cost tracking
├── state.py            # TriageState and Pydantic models
├── budget.py           # Token budget ContextVar
├── rag.py              # ChromaDB index + sentence-transformers retrieval
├── harvest.py          # Candidate PR harvesting with diversity constraints
├── prelabel.py         # Heuristic pre-labeling pipeline
├── aggregator.py       # Deterministic multi-critic aggregator
├── golden.py           # Golden fixture builder
├── eval.py             # Eval harness — runs pipeline against golden set
├── labeler_app.py      # Streamlit manual labeling tool
├── eval_viewer_app.py  # Streamlit eval results viewer
└── graph/
    ├── nodes.py        # LangGraph node functions (3 critics + classify + aggregate)
    └── pipeline.py     # StateGraph assembly, run_pipeline(), budget pre-check
tests/
└── fixtures/
    ├── golden/         # 53-entry golden set (JSON per PR)
    └── llm/            # Recorded LLM response sequences for --fake mode
```
