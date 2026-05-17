# ai-slop-detector

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-78%25-yellow.svg)](#development)
[![Status](https://img.shields.io/badge/status-v0.3.0-blue.svg)](#current-phase-status)

A CLI tool (and planned GitHub Action) that flags AI-slop pull requests using a multi-critic LangGraph pipeline with RAG. Built to help OSS maintainers handle the surge of low-effort, AI-generated PRs.

## What this is

Binary slop classifier for pull requests. `ai-slop-detector check <owner/repo> <pr_number>` fetches a PR from GitHub, retrieves project context from a per-repo ChromaDB index, classifies the PR size, runs two critics in parallel (`architecture_critic`, `slop_signals_critic`), and emits one of two verdicts:

- `approve` — not slop. Maintainers review as normal.
- `reject` — looks like AI slop. Maintainers can close or ask the author to revise.

The Action is designed to fire **once** per PR at `on: pull_request: [opened, reopened]`. At that moment maintainer comments, CI timing, and merge status don't exist yet — the critics judge from PR content + author signals + heuristics + RAG only ("first-look mode").

## Phase 3 deliverables

- **50-entry golden set** — 10 slop, 40 not-slop, spanning 8 repos (ruff, pydantic, poetry, godot, Home Assistant, ghostty, curl, tldraw). `is_slop: bool` is the label on every fixture.
- **Two critics in parallel** — `architecture_critic` (over-engineering, AI-explanatory docstrings, wrong-arch-layer) + `slop_signals_critic` (AI footer, drive-by overreach, manipulative @-mention, AI-checklist theatre, sibling-repo mismatch, heuristic features). LangGraph fan-out/fan-in with `operator.add` reducer.
- **Deterministic binary aggregator** — weighted score (slop 0.6, arch 0.4), veto rule (any critic ≤ 3 caps overall at 3), single `_SLOP_THRESHOLD = 5.0`. Output: `approve` or `reject`.
- **`ai-slop-detector eval`** — runs the pipeline against the golden set, emits binary precision/recall/F1 on the slop class plus a per-golden-class breakdown.
- **`ai-slop-detector view`** — Streamlit eval viewer. Shows slop precision/recall/F1, binary confusion matrix, and a disagreements table split into false positives and false negatives.
- **`ai-slop-detector label`** — Streamlit manual labeling tool. Binary verdict: 🗑️ Slop or ✅ Not slop. Output writes `{"repo", "pr_number", "is_slop"}` to `data/golden_labels.jsonl`.
- **`ai-slop-detector prelabel` / `harvest`** — automated harvest and heuristic pre-labeling pipeline. `prelabel` emits `{is_slop_likely, confidence, signals}` for each candidate.
- **`ai-slop-detector golden-build`** — CLI builder; validates min slop / not-slop counts; writes `is_slop` into every fixture.
- **Dollar cost guardrail** — `MAX_EVAL_COST_USD` in `.env` stops the eval loop before it exceeds budget.
- **Cost tracking** — `ClaudeClient.total_cost_usd`; printed after every eval run.

**Eval results (2026-05-16, first-look mode, full 50-entry golden set, Sonnet):**

| Metric | Value |
|---|---|
| Slop precision | **0.714** (10 TP / 14 flagged) |
| Slop recall | **1.000** (10/10 slop caught) |
| Slop F1 | **0.833** |
| Accuracy | 92.0% (46/50) |
| Cost per PR | ~$0.025–0.035 |

**All 10 slop PRs are caught with no false positives on clearly-accepted PRs.** The 4 false positives are on `is_slop=False` entries whose diffs carry slop-adjacent content (over-engineered patches, AI-style docstrings, drive-by overreach). The model can't see the maintainer's design reasoning but does see the slop-style content — so these FPs still surface PRs worth a closer look, not arbitrary noise.

## How scoring works

Each critic emits an integer score on **0–10** with these anchors:

| Score | Meaning |
|---|---|
| **10** | Exemplary contribution — clear intent, good engineering hygiene |
| **8**  | Solid — common case for legitimate PRs |
| **6**  | Neutral / borderline |
| **4**  | Significant slop markers — vague description, generic AI phrases, or one strong negative signal |
| **2**  | Clear slop — a hard cap fired (AI-disclosure footer, drive-by overreach, sibling-repo mismatch, manipulative @-mention, AI-checklist theatre) |
| **0**  | Pure boilerplate / wrong-target |

The aggregator combines the two critic scores deterministically:

- Weighted score = `0.4 × architecture_critic + 0.6 × slop_signals_critic`
- **Score ≥ 5.0** → `approve` (not slop, maintainer reviews as normal)
- **Score < 5.0** → `reject` (slop, flagged for the maintainer)
- **Veto rule**: any critic ≤ **3** caps the overall score at **3** → automatic reject. One strong slop signal from either critic alone forces a slop verdict.

Thresholds live in `src/ai_slop_detector/aggregator.py` as `_SLOP_THRESHOLD`, `_VETO_THRESHOLD`, and `_VETO_CAP`. Critic weights live in `_DEFAULT_WEIGHTS`. The same legend is available in the eval viewer under the Disagreements section.

## Phase 2 deliverables

- `ClaudeClient` — real Anthropic SDK calls, model routing (Sonnet for critics, Haiku for classification), tenacity retry on 429 / 5xx / connection errors, per-run token budget cap
- `RAGIndex` — ChromaDB persistent store at `data/chroma/`, sentence-transformers `all-MiniLM-L6-v2` embeddings, 800-1000 char paragraph chunks with heading prefix, per-repo indexing
- LangGraph pipeline — `ingest_pr → classify_size → retrieve_context → guidelines_critic → emit_verdict`
- CLI — `ai-slop-detector check`, `ai-slop-detector index`, global `--fake` flag for offline replay from `tests/fixtures/llm/`
- `--json` flag on `check` emits the full `TriageState` (consumed by Phase 4 GitHub Action)
- 91 tests (87% coverage; remaining gaps are live-API paths — GitHub, Anthropic, ChromaDB)

## Phase 1 deliverables

- GitHub PR ingestion via PyGithub
- `TriageState` Pydantic model
- `ClaudeClient` gateway (stub in Phase 1, real in Phase 2)
- Per-run token budget cap via `ContextVar`
- CLI: `ai-slop-detector fetch <owner/repo> <pr_number>` prints full JSON
- Pytest suite with `--fake` mode

## Non-goals

- Not a general-purpose code review tool
- No GitHub Action packaging yet (Phase 4)

## Current phase status

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | Done | Repo skeleton, GitHub ingestion, CLI |
| 2 | Done | LangGraph critic pipeline, RAG, single guidelines critic |
| 3 | Done | Binary slop classifier end-to-end (pipeline + prelabel + labeler + eval viewer + golden set + eval harness) |
| 4 | Planned | GitHub Action packaging (action.yml, marketplace) |

## Usage

```bash
# Install
pip install -e ".[dev]"

# Credentials — shell environment variables take priority.
# .env (if present) provides fallback values for variables not set in the shell.
cp .env.example .env   # fill in your tokens, never commit .env

# Index a repo's guidelines and recent PR history into ChromaDB
# (downloads all-MiniLM-L6-v2 on first run, ~90 MB)
ai-slop-detector index owner/repo

# Run the triage critic on a PR
ai-slop-detector check owner/repo 42

# JSON output — full TriageState, useful for scripting or Phase 4
ai-slop-detector check owner/repo 42 --json

# Dry-run without API calls — replays cached LLM responses
ai-slop-detector --fake check owner/repo 42

# Raise the token budget cap (default 50 000)
ai-slop-detector check owner/repo 42 --max-tokens 100000

# Run eval against the golden set (Sonnet, ~$1.50 for the full 50 — production-quality)
ai-slop-detector eval

# Cheap iteration with Haiku (~$0.30) — for fast feedback during prompt tweaks
ai-slop-detector eval --model haiku

# Browse eval results in Streamlit
ai-slop-detector view outputs/eval_runs/<run_id>.json

# Harvest candidate PRs from a repo
ai-slop-detector harvest owner/repo --max-prs 50

# Heuristic pre-labeling of harvested candidates
ai-slop-detector prelabel outputs/candidates/<repo>.jsonl

# Build a golden fixture from a labeled candidate
ai-slop-detector golden-build outputs/candidates/<repo>.jsonl --pr <number>

# Open the manual labeling UI
ai-slop-detector label outputs/candidates/<repo>.jsonl

# Phase 1: just fetch and inspect the raw state
ai-slop-detector fetch owner/repo 42
```

## Development

```bash
pip install -e ".[dev]"
pytest                    # unit + fake-mode tests, no live credentials needed
pytest --fake             # same, explicit flag
pytest --cov=src/ai_slop_detector --cov-report=term-missing
```

`pytest-cov` is included in the `dev` extra.

## Project structure

```
src/ai_slop_detector/
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
    ├── nodes.py        # LangGraph node functions (classify + 2 critics + aggregate)
    └── pipeline.py     # StateGraph assembly, run_pipeline(), budget pre-check
tests/
└── fixtures/
    ├── golden/                            # 50-entry golden set (JSON per PR, is_slop labels)
    └── llm/                                # Recorded LLM response sequences for --fake mode
```
