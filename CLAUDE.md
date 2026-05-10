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
├── cli.py           # Typer entry point
├── github_client.py # PyGithub wrapper — all GitHub I/O
├── claude_client.py # Single gateway for all Claude API calls
├── state.py         # TriageState Pydantic model
└── budget.py        # BudgetContext ContextVar + BudgetExceeded
```

## Phase status

- **Phase 1 (current):** repo skeleton, ClaudeClient stub, GitHub PR ingestion, CLI `fetch` command.
- Phase 2: multi-agent LangGraph critic pipeline.
- Phase 3: RAG context retrieval.
- Phase 4: GitHub Action packaging.
