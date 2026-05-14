# Golden Set Sources

This directory contains the golden test fixtures used by the Phase 3 eval harness.

## How entries are created

1. **Harvest** candidate PRs with `pr-triage harvest <owner/repo> --out-dir data/candidates/`
2. **Pre-label** heuristically with `pr-triage prelabel`
3. **Review** pre-labels in `data/pre_labels.jsonl`, correct labels, save to `data/golden_labels.jsonl`
4. **Build** final fixtures with `pr-triage golden-build`

## Requirements

- Minimum 30 entries total
- At least 5 entries per class (approve / request_changes / reject)

## Repos included

| Repo | PRs | Notes |
|------|-----|-------|
| _(populated after first golden-build run)_ | | |

## Label distribution

| Label | Count |
|-------|-------|
| approve | |
| request_changes | |
| reject | |
