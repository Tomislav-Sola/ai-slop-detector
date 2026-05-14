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

## Class definitions with calibration examples

Each class has one anchor example with the actual PR URL and a brief
explanation of why it belongs to that class. These cases were manually
verified during candidate inspection and are referenced for critic
calibration. If a future critic mis-classifies any of these anchors,
the critic is fundamentally broken.

### accepted

**Anchor: [astral-sh/ruff#24960](https://github.com/astral-sh/ruff/pull/24960)**
- Author: MichaReiser (MEMBER, ~1000 prior PRs)
- +601 / -322 across 12 files, merged after ~13 days
- Substantial refactor with clear technical writeup in PR body
- 9 inline review comments, 3 issue comments — real maintainer engagement
- Tests added, multi-commit structure, linked cross-repo issue (astral-sh/ty#1950)
- Why accepted: substantive contribution from a core maintainer, full
  technical discussion, clean merge.

**Second anchor (zero-comment accepted): [ghostty-org/ghostty#12518](https://github.com/ghostty-org/ghostty/pull/12518)**
- Author: knu (CONTRIBUTOR, 2 prior PRs)
- +91 / -11, merged within ~24h
- Zero comments on PR, but body explicitly discloses AI use:
  "AI usage: OpenAI Codex helped investigate, implement, test, and refine
  this change. I reviewed and tested the resulting code."
- References a vouched discussion issue (#12169)
- Tests added with 8 parametrised cases including edge cases
- Why accepted: correctly disclosed AI-assisted contribution, followed
  Ghostty's vouch process, technically sound. Critical calibration case:
  prevents the critic from learning "any AI disclosure = slop". The signal
  is disclosure quality and review evidence, not the presence of the word AI.

### rejected_quality

*(To be filled in during labeling with one verified example.)*

### slop

**Anchor: [ghostty-org/ghostty#10515](https://github.com/ghostty-org/ghostty/pull/10515)**
- Author: mvanhorn (NONE, 1 prior PR)
- +345 / -0 across 4 files, closed in 13 minutes by Mitchell Hashimoto
- PR body footer: "Generated with Claude Code" and "Co-Authored-By: Claude"
- Closing maintainer comment cites the AI policy verbatim:
  "as noted by the AI policy, any AI assisted PRs need to be an accepted
  issue not random PRs"
  "Closing this due to violating our policies. There was a lot of failure
  to read the instructions here which is highly questionable"
- The PR implemented an SCP/ControlMaster image-paste hack for a problem
  that is already solved by an established protocol (OSC 5522), which the
  submitter himself acknowledged after the close:
  "My SCP/ControlMaster approach works but it's definitely a hack"
- Why slop: pure-add (+345/-0), undisclosed AI generation that violated
  stated policy, reinvented an existing protocol, closed in minutes by
  the owner with explicit policy citation. Triggers all three slop
  sub-patterns: ai_disclosure_or_mention (Claude footer),
  maintainer_explicit_rejection (policy citation), and structural slop
  signals (large pure-add for already-solved problem).

**Second anchor (silent-slop variant): [pydantic/pydantic#13100](https://github.com/pydantic/pydantic/pull/13100)**
*(verify during labeling — likely fits the silent-slop pattern: first-time
author, substantive diff, closed unmerged with zero comments)*

### Anti-anchors (what NOT to classify as slop)

**[ghostty-org/ghostty#12518](https://github.com/ghostty-org/ghostty/pull/12518)** — already cited under accepted above.
Disclosed AI use that was merged. The critic must distinguish this from #10515.

**[tldraw/tldraw#8671](https://github.com/tldraw/tldraw/pull/8671)** — author steveruizok (Founder, >1000 prior PRs),
+0 / -3, no comments, closed unmerged. This is a maintainer cleanup PR
abandoned for unknown reasons, NOT slop. Critics should treat
closed-unmerged maintainer-authored PRs differently from contributor PRs.
