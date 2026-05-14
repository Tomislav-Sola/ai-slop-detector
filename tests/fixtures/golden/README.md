# Golden Set — Methodology

This directory contains the final golden test fixtures used by the Phase 3 eval harness.
Each file is a merged PR candidate JSON annotated with a `golden_label` field.

## Construction pipeline

```
pr-triage harvest <repos>   →   data/candidates/
pr-triage prelabel          →   data/pre_labels.jsonl
[manual review + edits]     →   data/golden_labels.jsonl
pr-triage golden-build      →   tests/fixtures/golden/
```

## Methodological choices

### Settle-time filter (14 days)

PRs are excluded if `closed_at > now() - 14 days` at harvest time.

**Why:** A PR merged today may be reverted next week, receive follow-up "this broke X"
issues, or trigger a hotfix PR. Labels assigned within hours of closing are noisy.
14 days gives enough buffer for:
- CI instability to surface on the target branch
- Dependent teams to catch regressions
- The original author to push fixes

Override with `--min-age-days 0` if you intentionally want fresh PRs.

### Closed-only (no open PRs)

Only PRs in `state=closed` (merged or rejected) are harvested.

**Why:** Open PRs have no decided outcome. A PR that is still open cannot be labeled
`approve` or `request_changes` — it represents an undecided case and adds noise to
evaluation.

### Cross-repo linked issues

Cross-repo issue references (e.g. `Closes https://github.com/owner/other/issues/42`)
are stored in `linked_issues` with `repo="owner/other"` and `title=null`.
The body is not fetched to avoid cross-repo API calls during harvest.

### Bot comments

Comments from accounts ending in `[bot]` (CI bots, automation) are stored in a
separate `bot_comments` field and excluded from `issue_comments`. This keeps
`issue_comments` clean for maintainer-voice analysis.

## Class distribution requirements

| Label | Minimum |
|-------|---------|
| approve | 5 |
| request_changes | 5 |
| reject | 5 |
| **total** | **30** |

## Sources

See [SOURCES.md](SOURCES.md) for the actual repos and PR numbers included.
