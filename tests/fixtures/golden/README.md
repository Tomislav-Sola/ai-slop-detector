# Golden Set — Methodology

This directory contains the golden test fixtures used by the eval harness.
Each `.json` file is a PR candidate annotated with `is_slop: bool`.
`manifest.json` is generated automatically by `ai-slop-detector golden-build`.

## Construction pipeline

```
ai-slop-detector harvest <repos> --out-dir data/golden_candidates_v2/
ai-slop-detector prelabel             →  data/pre_labels_v2.jsonl
ai-slop-detector label                →  (manual review via Streamlit)  →  data/golden_labels.jsonl
ai-slop-detector golden-build         →  tests/fixtures/golden/
```

## Current set

| Metric | Value |
|--------|-------|
| Total entries | 50 |
| is_slop=True | 10 |
| is_slop=False | 40 |
| Distinct repos | 8 |

### Per-repo breakdown

| Repo | Entries |
|------|---------|
| pydantic/pydantic | 11 |
| astral-sh/ruff | 9 |
| python-poetry/poetry | 7 |
| home-assistant/core | 6 |
| godotengine/godot | 6 |
| ghostty-org/ghostty | 5 |
| curl/curl | 4 |
| tldraw/tldraw | 2 |

## Label definitions

### is_slop = True

PR was closed with explicit or implicit evidence of low-effort or
AI-assisted generation without meaningful human validation.

Sub-patterns represented: explicit AI policy rejection, silent slop
(first-timer / no comments / AI footer), bot-spam recidivism,
corporate-vendor astroturfing.

### is_slop = False

Everything else — merged PRs *and* closed-unmerged PRs that were rejected
for code/design quality, scope, or architectural disagreement rather than
AI-slop signals. The Action's job is to flag slop only; maintainers review
the rest as normal.

## Methodological choices

### Settle-time filter (14 days)

PRs closed fewer than 14 days before harvest time are excluded.

### Closed-only

Only `state=closed` PRs (merged or unmerged). Open PRs have no decided outcome.

### Human-in-the-loop labeling

No entry is labeled without human review. The prelabeler suggests
`is_slop_likely` and confidence; the final binary `is_slop` is always
written by a human via the Streamlit tool.

### Diversity constraints

The v2 harvest applies per-author (max 2), per-repo (max 30–40), and
per-(author, repo) limits so no single prolific contributor dominates.

## Known limitations

- English-only repos
- 8 repos is a small sample — results will not generalise to embedded,
  ML-pipeline, or domain-specific projects
- Labels reflect a single labeler's judgment as ground truth
- The slop class is over-represented relative to real-world repos — most
  OSS repos see slop at well below 20% of PR volume

## How to extend

1. `ai-slop-detector harvest <owner/repo> --out-dir data/golden_candidates_v2/`
2. `ai-slop-detector prelabel`
3. `ai-slop-detector label`
4. `ai-slop-detector golden-build`
