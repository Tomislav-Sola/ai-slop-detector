# Contributing

Thanks for considering a contribution. This document covers the setup, the tests, the eval loop, the golden-set extension flow, and the commit-message style.

## Local setup

Python 3.11+ required.

```bash
git clone https://github.com/Tomislav-Sola/ai-slop-detector
cd ai-slop-detector
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,eval]"
```

The `dev` extra brings pytest + coverage. The `eval` extra brings Streamlit (used by `ai-slop-detector view` and `ai-slop-detector label`).

Credentials come from your shell environment:

```bash
export ANTHROPIC_API_KEY="sk-..."     # for live pipeline + eval runs
export GITHUB_TOKEN="ghp_..."         # for fetching PRs + indexing repos
```

`.env` provides fallbacks for any variable not set in the shell — copy `.env.example` to `.env` and fill in. **Never commit `.env`.**

## Running tests

```bash
pytest                                            # default: fake mode, no live credentials needed
pytest --fake                                     # same, explicit
pytest --cov=src/ai_slop_detector --cov-report=term-missing
pytest tests/test_action_entrypoint.py -v         # one module
```

The test suite is fake-mode by default. LLM responses are replayed from `tests/fixtures/llm/<repo>_pr<number>.json`. If you change a critic prompt or graph wiring, you may need to re-record the fixtures — see the `--fake` flag plumbing in `src/ai_slop_detector/claude_client.py`.

## Running the eval

The eval runs the full pipeline against the 50-entry golden set in `tests/fixtures/golden/`. It hits the live Anthropic API.

```bash
ai-slop-detector eval                             # Sonnet, ~$1.50 for the full set
ai-slop-detector eval --model haiku               # Haiku, ~$0.30 — cheap iteration
ai-slop-detector eval --limit 10                  # subset for fast smoke checks
```

Results land in `outputs/eval_runs/<timestamp>.json`. Browse them with:

```bash
ai-slop-detector view outputs/eval_runs/<timestamp>.json
```

The Streamlit viewer shows the binary confusion matrix, precision/recall/F1 on the slop class, and a disagreements table split into false positives and false negatives — useful for spotting prompt regressions.

`MAX_EVAL_COST_USD` in `.env` is the hard dollar guardrail; the loop stops before exceeding it.

## Proposing a new golden-set fixture

The golden set defines what "slop" and "not slop" mean operationally. Adding a fixture is a meaningful change — open an issue first if you want to discuss whether a candidate belongs.

The full flow:

```bash
# 1. Harvest candidates from a repo (uses GITHUB_TOKEN; diversity-aware).
ai-slop-detector harvest owner/repo --max-prs 50

# 2. Heuristic pre-labeling — emits is_slop_likely + confidence + signals.
ai-slop-detector prelabel

# 3. Manual review in the Streamlit labeler. Binary verdict: Slop / Not slop.
ai-slop-detector label data/pre_labels_v2.jsonl

# 4. Build the fixture from your labels.
ai-slop-detector golden-build
```

`golden-build` validates the minimum slop / not-slop counts and writes `is_slop` into each fixture JSON. Per-fixture provenance lives in `tests/fixtures/golden/SOURCES.md` — please add an entry there explaining why the example is in the set.

Re-run the eval after adding fixtures and include the new precision/recall/F1 numbers in your PR description.

## Commit style

Conventional commits, lowercase, no `Co-Authored-By` trailer:

- `feat:` new user-facing capability
- `fix:` bug fix
- `refactor:` internal change with no external effect
- `docs:` README, CONTRIBUTING, comments
- `test:` test-only changes
- `chore:` build, deps, version bumps, generated artefacts

Em-dashes (`—`) are used freely in subjects and bodies. Wrap commit bodies at ~72-80 characters. Bodies on non-trivial commits should be a bullet list of affected areas; see recent commits for examples.

## Code of conduct

Be specific, be honest, be kind. Concrete failure cases beat generic feedback. The maintainer reserves the right to close low-effort contributions — including, with appropriate irony, AI-generated PRs that this project itself would flag as slop.

Disagreement is welcome; argue the substance, not the person.

## Licensing

By submitting a contribution you agree it is licensed under the [MIT License](LICENSE), same as the rest of the project.
