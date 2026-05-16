# Golden Set Sources

This directory contains the golden test fixtures used by the eval harness for the binary AI-slop classifier. Each fixture carries `is_slop: bool` as its only label.

## How entries are created

1. **Harvest** candidate PRs with `pr-triage harvest <owner/repo> --out-dir data/candidates/`
2. **Pre-label** heuristically with `pr-triage prelabel`. Each output row carries `{is_slop_likely, confidence, signals}`.
3. **Review** pre-labels in the Streamlit labeler. Each saved row in `data/golden_labels.jsonl` is `{repo, pr_number, is_slop}` (or `{skip: true}` for skips).
4. **Build** final fixtures with `pr-triage golden-build`.

## Requirements

- Minimum 30 entries total
- At least 5 entries on each side of the binary split (`is_slop=True` / `is_slop=False`)

## Repos included

| Repo | Active fixtures | Notes |
|------|-----------------|-------|
| pydantic/pydantic    | 11 | Python; silent-slop pattern prominent |
| astral-sh/ruff       |  9 | Rust + Python; high merged-PR rate, AI policy enforced |
| python-poetry/poetry |  7 | Python; moderate PR volume |
| home-assistant/core  |  6 | Python; high PR churn, settle-filter heavy |
| godotengine/godot    |  6 | GDScript + C++; diverse PR types |
| ghostty-org/ghostty  |  5 | Explicit AI policy; strong explicit-rejection signal |
| curl/curl            |  4 | C codebase; strong rejection rationale in comments |
| tldraw/tldraw        |  2 | TypeScript; React-heavy codebase |
| **Total**            | **50** | |

## Label distribution

| Field | Count |
|-------|-------|
| `is_slop=True`  | 10 |
| `is_slop=False` | 40 |

## Slop class methodology

Slop candidates were identified by three independent signal heuristics in `prelabel.py`:

1. **AI-disclosure signal** (`ai_disclosure_or_mention`): closed-unmerged PRs whose body or comments contain keywords like AI-generated, ChatGPT, Claude, Copilot, Codex, "Generated with Claude Code", "Co-Authored-By: Claude", etc. Medium confidence per match; not all matches are slop (legitimate disclosed AI use that was merged is filtered out by the `merged: false` requirement).

2. **Silent-slop signal** (`silent_slop_pattern`): closed-unmerged PRs with substantive diff (20–500 lines), first-time or near-first-time author (≤3 prior PRs), and zero maintainer or bot comments. Low confidence per match — these are slop-silence closures common in pydantic and ghostty, but the pattern also matches some legitimate closures (duplicate, won't-fix). Manual verification required.

3. **Maintainer-explicit-rejection signal** (`maintainer_explicit_rejection`): closing maintainer comment within 24h of closure citing the AI policy, CONTRIBUTING, "drive-by", "did you write this yourself", or similar. High confidence per match — strongest ground-truth signal available.

Signals stack: a PR matching two or three signals receives proportionally higher confidence. Each candidate flagged by any signal was manually verified before inclusion in the final golden set.

## Known methodological characteristics

- **Slop class size is small.** Slop is rarer than legitimate contribution and harder to ground-truth.

- **Slop class is biased toward identifiable slop.** Cases the maintainer corpus could surface as slop (explicit policy citations, AI-disclosure in bodies, silent closures) dominate. "Undetectable slop" that was merged is by definition absent from this set.

- **First-look detectability.** The classifier runs in first-look mode (`on: pull_request: [opened, reopened]`). Slop signals that exist only post-hoc — close timing, coverage-bot reports, maintainer thread comments — are out of scope.

- **Repo distribution reflects maintainer policy.** ghostty-org/ghostty and curl/curl have public anti-slop policies and contribute most explicit-rejection cases. pydantic/pydantic contributes most silent-slop cases. godotengine/godot and astral-sh/ruff contribute most well-discussed accepted cases.

- **English-only.** All sampled repos use English in PR discussions. Critic generalization to other languages is untested.

- **Public repos only.** No private or enterprise-repo behavior is represented.

- **Labels reflect a single labeler's judgment.** No inter-rater reliability check was performed.
