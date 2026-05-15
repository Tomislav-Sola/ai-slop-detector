# CLAUDE.md — pr-triage

## Ground rules

- **Python 3.11+, pip + pyproject.toml. Never Poetry.**
- **No `pip install` or `git` commands without explicit user approval.**
- Never commit `.env`, `data/`, `outputs/`, `*.db`.
- `.gitignore` must cover `.env`, `.env.*`, `.coverage`, `outputs/`, `data/`, `*.db`, `.venv/` before any matching file is created.
- `ANTHROPIC_API_KEY` and `GITHUB_TOKEN` come from shell env — never from repo files.
- All Claude API calls go through `ClaudeClient` in `src/pr_triage/claude_client.py`. Never instantiate `Anthropic()` elsewhere.
- Per-run hard token budget cap via `ContextVar` (`src/pr_triage/budget.py`).
- Run `pytest` after each meaningful change.
- Conventional commits: `feat` / `fix` / `chore` / `docs` / `test` / `refactor`.
- Honest README: no "production-ready", no unmeasured performance claims, no em-dash separators in headings.

## Architecture

```
src/pr_triage/
├── cli.py              # Typer entry point
├── github_client.py    # PyGithub wrapper — all GitHub I/O
├── claude_client.py    # Single gateway for all Claude API calls; tracks cost_usd
├── state.py            # TriageState Pydantic model
├── budget.py           # BudgetContext ContextVar + BudgetExceeded
├── rag.py              # ChromaDB RAG index + sentence-transformers retrieval
├── harvest.py          # GitHub PR harvester with DiversityConfig/DiversityTracker
├── prelabel.py         # Heuristic pre-labeler for golden-set candidates
├── aggregator.py       # Deterministic multi-critic aggregator (weights, veto rule)
├── eval.py             # Eval harness: load golden set, run pipeline, compute metrics
├── labeler_app.py      # Streamlit manual labeling tool (pr-triage label)
├── eval_viewer_app.py  # Streamlit eval results viewer (pr-triage view)
└── graph/
    ├── nodes.py        # LangGraph node functions (classify, 3 critics, aggregate)
    └── pipeline.py     # StateGraph assembly, run_pipeline(), critic_model override
```

## Model routing

- `classify_size` → Haiku (always — simple classification)
- `guidelines_critic` → Sonnet (production default, cross-references CONTRIBUTING.md chunks)
- `architecture_critic` → Sonnet (production default, pattern consistency judgment)
- `slop_signals_critic` → Haiku (production default, heuristics carry most weight)
- `pr-triage eval` → all critics default to Haiku; use `--model sonnet` for quality check

## Phase status

- **Phase 1 (done):** repo skeleton, ClaudeClient stub, GitHub PR ingestion, CLI `fetch`.
- **Phase 2 (done):** real ClaudeClient, ChromaDB RAG, LangGraph single-critic pipeline, CLI `check` + `index`.
- **Phase 3 (done):** golden-set construction (53 entries, 8 repos), multi-critic pipeline (guidelines + architecture + slop_signals), deterministic aggregator, eval harness + Streamlit viewer, per-critic model split, dollar cost guardrail.
- **Phase 4:** GitHub Action packaging.
