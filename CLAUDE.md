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

## Product framing

- **Binary slop classifier.** Output is `is_slop: bool` (`approve` = not slop / `reject` = slop). No 3-class output. RQ and accepted both fold to not-slop because:
  - Maintainers still review not-slop PRs as normal — the Action's job is filtering AI slop, not arbitrating quality.
  - At PR-open time (when the Action fires), RQ vs accepted is structurally indistinguishable from content alone.
- **First-look mode only.** The pipeline runs once per PR, triggered by `on: pull_request: [opened, reopened]`. No re-triage; closing/comments/timing are not used by critics or the aggregator.
- **Two critics, parallel fan-out.** `architecture_critic` (over-engineering, AI-explanatory docstrings, wrong-arch-layer) + `slop_signals_critic` (AI footer, drive-by overreach, manipulative @-mention, AI-checklist theatre, sibling-repo mismatch, heuristics). `guidelines_critic` was dropped — RQ-territory, marginal signal for slop.

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
├── golden.py           # build_golden_set; validates min slop / not-slop counts
├── aggregator.py       # Deterministic binary aggregator (veto rule, _SLOP_THRESHOLD)
├── eval.py             # Eval harness: load golden set, run pipeline, binary metrics
├── labeler_app.py      # Streamlit manual labeling tool (pr-triage label)
├── eval_viewer_app.py  # Streamlit eval results viewer (pr-triage view)
└── graph/
    ├── nodes.py        # LangGraph node functions (classify + 2 critics + aggregate)
    └── pipeline.py     # StateGraph assembly, run_pipeline(), critic_model override
```

## Labels

- **Primary:** `is_slop: bool` on every golden fixture and label entry.
- **Legacy:** `golden_label` (`accepted` | `rejected_quality` | `slop`) is kept as auxiliary metadata for analysis (the original Opus-4.7 labeling rationale lives in `label_notes` per entry).
- New code paths must read `is_slop` directly; only fall back to deriving from `golden_label` for backward compat with older fixtures.

## Model routing

- `classify_size` → Haiku (always — simple classification)
- `architecture_critic` → Sonnet (production default)
- `slop_signals_critic` → Sonnet (production default; Haiku reject recall collapsed to 0.077 in earlier eval)
- `pr-triage eval` → critics default to Haiku for cheap iteration; use `--model sonnet` for production-quality check

## Phase status

- **Phase 1 (done):** repo skeleton, ClaudeClient stub, GitHub PR ingestion, CLI `fetch`.
- **Phase 2 (done):** real ClaudeClient, ChromaDB RAG, LangGraph single-critic pipeline, CLI `check` + `index`.
- **Phase 3 (done):** golden-set construction (50 entries, 8 repos, 10 slop / 40 not-slop), binary slop classification end-to-end (core pipeline + prelabel + labeler + eval viewer + eval harness), first-look mode, two-critic pipeline (architecture + slop_signals), deterministic binary aggregator (veto rule + single `_SLOP_THRESHOLD`), dollar cost guardrail. All tools emit `is_slop` as primary while keeping the legacy 3-class label as auxiliary metadata. Full-set eval at PR-open (Sonnet): precision 0.714, recall 1.000, F1 0.833, accuracy 92.0% (46/50), ~$0.03/PR. Three slop fixtures whose signals require post-hoc data archived under `tests/fixtures/golden_archive_post_hoc_only/`.
- **Phase 4:** GitHub Action packaging — `action.yml`, Dockerfile, marketplace publish, optional `is_slop` label-write or PR-comment in the target repo.
