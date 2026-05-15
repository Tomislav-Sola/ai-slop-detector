# Golden Set — Methodology

This directory contains the Phase 3 golden test fixtures used by the eval harness.
Each `.json` file is a PR candidate annotated with `golden_label` and `label_notes`.
`manifest.json` is generated automatically by `pr-triage golden-build`.

## Construction pipeline

```
pr-triage harvest <repos> --out-dir data/golden_candidates_v2/
pr-triage prelabel             →  data/pre_labels_v2.jsonl
pr-triage label                →  (manual review via Streamlit)  →  data/golden_labels.jsonl
pr-triage golden-build         →  tests/fixtures/golden/
```

## Current set

| Metric | Value |
|--------|-------|
| Total entries | 53 |
| accepted | 20 |
| rejected_quality | 20 |
| slop | 13 |
| Distinct repos | 8 |

### Per-repo breakdown

| Repo | Entries |
|------|---------|
| pydantic/pydantic | 13 |
| astral-sh/ruff | 9 |
| python-poetry/poetry | 7 |
| godotengine/godot | 6 |
| home-assistant/core | 6 |
| ghostty-org/ghostty | 5 |
| curl/curl | 5 |
| tldraw/tldraw | 2 |

## Label definitions

### accepted

PR was merged with no revert within 14 days. Covers first-time-contributor
bug fixes through founder/member architectural work. Internal-team chore PRs
are included for repos with closed external-contributor policies (tldraw).

### rejected_quality

PR was closed unmerged for code/design quality, scope, or architectural
disagreement — **not** because of AI slop signals.

Sub-patterns represented: maintainer design rejection, superseded by maintainer
alternative, policy rejection (wrong submission channel), collaborative iteration
that ultimately diverged, trusted-contributor design pushback.

### slop

PR was closed with explicit or implicit evidence of low-effort or
AI-assisted generation without meaningful human validation.

Sub-patterns represented: explicit AI policy rejection, 58-second closure smoking
gun, silent slop (first-timer / no comments / AI footer), bot-spam recidivism,
corporate-vendor astroturfing, fabricated tool name in security report.

## Methodological choices

### Settle-time filter (14 days)

PRs closed fewer than 14 days before harvest time are excluded.

### Closed-only

Only `state=closed` PRs (merged or unmerged). Open PRs have no decided outcome.

### Human-in-the-loop labeling

No entry is labeled without human review. The prelabeler suggests a label and
confidence; the final label is always written by a human via the Streamlit tool.
This avoids the methodological circularity of auto-labeling eval data.

### Diversity constraints

The v2 harvest applies per-author (max 2), per-repo (max 30–40), and
per-(author, repo) limits so no single prolific contributor dominates.

## Known limitations

- English-only repos
- 8 repos is a small sample — results will not generalise to embedded,
  ML-pipeline, or domain-specific projects
- Labels reflect a single labeler's judgment as ground truth
- slop is over-represented (13 vs target 10) because slop is easier to
  identify with high confidence than borderline rejected_quality cases
- tldraw has only 2 entries because the repo rejects all external contributors

## How to extend

1. `pr-triage harvest <owner/repo> --out-dir data/golden_candidates_v2/`
2. `pr-triage prelabel`
3. `pr-triage label`
4. `pr-triage golden-build`
