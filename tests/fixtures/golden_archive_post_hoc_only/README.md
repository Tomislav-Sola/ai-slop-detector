# Archived slop fixtures — post-hoc signals only

These three slop fixtures were removed from the active golden set on 2026-05-16 because their slop signals are not available at PR-open time. They are preserved here for reference (the Opus-4.7 labeling rationale in each `label_notes` is valuable) and for any future re-triage mode that uses post-PR signals.

| Fixture | Why excluded |
|---|---|
| `curl__curl_pr21465.json` | Signal = fabricated security framework name ("VORTIQ-X VXF Framework"). Requires external knowledge to detect; the PR body's technical content (MQTT spec violation) reads as legitimate. Marketing-style non-human phrases appear in the maintainer thread, not the PR body. |
| `pydantic__pydantic_pr13083.json` | Signal = coverage-bot report (38% new-statement coverage) + 14-day silent close. The drive-by + AI-checklist body signals exist at PR-open, but the slop critic scored borderline (5–6) under current prompts. Effectively undetectable in v0.3.0 first-look mode. |
| `pydantic__pydantic_pr13100.json` | Signal = 58-second auto-close. Sole smoking gun is timing, which doesn't exist at `on: pull_request: opened`. |

If you re-introduce a re-triage mode (running after comments/timing accumulate), move these back into `tests/fixtures/golden/` and rebuild the manifest.
