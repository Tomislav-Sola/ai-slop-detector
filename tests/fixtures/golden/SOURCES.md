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
| astral-sh/ruff | 60 | High merged-PR rate; varied code size |
| curl/curl | 50 | C codebase; strong rejection rationale in comments |
| ghostty-org/ghostty | 40 | Explicit AI policy; strong explicit-rejection signal |
| godotengine/godot | 30 | GDScript + C++; diverse PR types |
| home-assistant/core | 30 | Python; high PR churn, settle-filter heavy |
| pydantic/pydantic | 30 | Python; silent-slop pattern prominent |
| python-poetry/poetry | 20 | Python; moderate PR volume |
| tldraw/tldraw | 40 | TypeScript; React-heavy codebase |

## Label distribution

| Label | Count |
|-------|-------|
| approve | _(populated after golden-build)_ |
| request_changes | |
| reject | |

## Slop class methodology

Slop candidates were identified by three independent signal heuristics
in `prelabel.py`:

1. **AI-disclosure signal** (`ai_disclosure_or_mention`): closed-unmerged
   PRs whose body or comments contain keywords like AI-generated, ChatGPT,
   Claude, Copilot, Codex, "Generated with Claude Code", "Co-Authored-By:
   Claude", etc. Medium confidence per match; not all matches are slop
   (e.g. legitimate disclosed AI use that was merged is filtered out by
   the `merged: false` requirement).

2. **Silent-slop signal** (`silent_slop_pattern`): closed-unmerged PRs
   with substantive diff (20–500 lines), first-time or near-first-time
   author (≤3 prior PRs), and zero maintainer or bot comments. Low
   confidence per match — these are slop-silence closures common in
   Pydantic and Ghostty, but the pattern also matches some legitimate
   closures (duplicate, won't-fix). Manual verification required.

3. **Maintainer-explicit-rejection signal**
   (`maintainer_explicit_rejection`): closing maintainer comment within
   24h of closure citing the AI policy, CONTRIBUTING, "drive-by", "did
   you write this yourself", or similar. High confidence per match,
   strongest ground-truth signal available.

Signals stack: a PR matching two or three signals receives proportionally
higher confidence.

Each candidate flagged by any signal was manually verified before
inclusion in the final golden set.

## Known methodological characteristics

- **Slop class size is small.** Slop is rarer than legitimate
  contribution and harder to ground-truth. Target distribution
  approximately 20 accepted, 20 rejected_quality, 10 slop, but actual
  distribution depends on what the labeling pass confirms.

- **Slop class is biased toward identifiable slop.** Cases the maintainer
  corpus could surface as slop (explicit policy citations, AI-disclosure
  in bodies, silent closures) dominate. "Undetectable slop" that was
  merged is by definition absent from this set.

- **Repo distribution reflects maintainer policy.** ghostty-org/ghostty
  and curl/curl have public anti-slop policies and contribute most
  explicit-rejection cases. pydantic/pydantic contributes most
  silent-slop cases. godotengine/godot and astral-sh/ruff contribute
  most well-discussed accepted cases.

- **English-only.** All sampled repos use English in PR discussions.
  Critic generalization to other languages is untested.

- **Public repos only.** No private or enterprise-repo behavior is
  represented.

- **Labels reflect a single labeler's judgment.** No inter-rater
  reliability check was performed.
