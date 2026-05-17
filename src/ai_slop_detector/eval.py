"""Eval harness for the multi-critic triage pipeline (Phase 3 / B5).

Usage:
    ai-slop-detector eval [--ablation critic_name] [--limit N] [--golden-dir PATH]

Loads every golden fixture, reconstructs a TriageState, runs the pipeline
(real LLM or fake mode), and computes precision / recall / F1 per class plus
a confusion matrix.  Results are written to outputs/eval_runs/<timestamp>.json.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ai_slop_detector.state import (
    CriticOutput,
    PRMetadata,
    TriageState,
)

_GOLDEN_DIR = Path("tests/fixtures/golden")
_OUTPUT_DIR = Path("outputs/eval_runs")

# Binary decision classes for the slop confusion matrix.
_DECISIONS = ["approve", "reject"]


def _expected_decision(entry: dict) -> str:
    """Map a fixture entry to the expected binary decision."""
    return "reject" if entry.get("is_slop", False) else "approve"


def load_golden_entries(golden_dir: Path = _GOLDEN_DIR) -> list[dict]:
    """Return all golden fixture dicts (excluding manifest.json)."""
    entries = []
    for f in sorted(golden_dir.glob("*.json")):
        if f.name == "manifest.json":
            continue
        entries.append(json.loads(f.read_text()))
    return entries


def entry_to_state(entry: dict) -> TriageState:
    """Reconstruct a TriageState from a golden fixture dict."""
    # First-look mode: at PR-open time, merged is always False. Don't leak the
    # eventual outcome into the state.
    meta = PRMetadata(
        number=entry["pr_number"],
        title=entry["title"],
        body=entry.get("body"),
        author=entry.get("author", "unknown"),
        author_association=entry.get("author_association"),
        created_at=_parse_dt(entry.get("created_at")),
        updated_at=_parse_dt(entry.get("updated_at")),
        base_branch=entry.get("base_branch", "main"),
        head_branch=entry.get("head_branch", "patch"),
        additions=entry.get("additions", 0),
        deletions=entry.get("deletions", 0),
        changed_files=entry.get("changed_files", 0),
        labels=entry.get("labels", []),
        draft=entry.get("draft", False),
        merged=False,
    )

    files_changed: list[str] = []
    for f in (entry.get("files_changed") or []):
        if isinstance(f, dict):
            files_changed.append(f.get("filename", ""))
        else:
            files_changed.append(str(f))

    # First-look mode: do NOT populate post-hoc fields (comments, closed_at). The
    # production trigger is `on: pull_request: [opened, reopened]` where these
    # do not exist yet. The eval simulates that.
    return TriageState(
        repo=entry["repo"],
        pr_number=entry["pr_number"],
        metadata=meta,
        raw_diff=entry.get("raw_diff", ""),
        files_changed=files_changed,
        author_prior_prs=entry.get("author_prior_prs_in_repo", 0),
        contributing_md=entry.get("contributing_md"),
        agents_md=entry.get("agents_md"),
    )


def _parse_dt(val):
    from datetime import datetime, timezone
    if val is None:
        return datetime.now(tz=timezone.utc)
    if isinstance(val, datetime):
        return val
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(tz=timezone.utc)


def compute_metrics(
    results: list[dict],
    *,
    skip_critics: set[str] | None = None,
) -> dict:
    """Compute binary slop-detection metrics: precision / recall / F1 on the slop class.

    Each result dict must have:
      - is_slop: bool  (the ground-truth label)
      - predicted_decision: str  ("approve" or "reject")
    """
    # Binary confusion: rows=true is_slop, cols=predicted is_slop.
    confusion: dict[str, dict[str, int]] = {
        true: {pred: 0 for pred in _DECISIONS}
        for true in _DECISIONS
    }

    for r in results:
        true_dec = "reject" if r.get("is_slop", False) else "approve"
        pred_dec = r.get("predicted_decision", "approve")
        if true_dec in confusion and pred_dec in confusion[true_dec]:
            confusion[true_dec][pred_dec] += 1

    # Binary precision/recall/F1 on the slop class (reject = positive).
    tp = confusion["reject"]["reject"]
    fp = confusion["approve"]["reject"]
    fn = confusion["reject"]["approve"]
    tn = confusion["approve"]["approve"]
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) > 0 else 0.0)

    total = len(results)
    correct = tp + tn
    accuracy = correct / total if total > 0 else 0.0

    return {
        "accuracy": round(accuracy, 3),
        "slop_precision": round(precision, 3),
        "slop_recall": round(recall, 3),
        "slop_f1": round(f1, 3),
        "confusion_matrix": confusion,  # binary 2x2
        "n_total": total,
        "n_correct": correct,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def run_eval(
    *,
    golden_dir: Path = _GOLDEN_DIR,
    out_dir: Path = _OUTPUT_DIR,
    ablation_critic: str | None = None,
    limit: int | None = None,
    verbose: bool = False,
    critic_model: str | None = None,
) -> dict:
    """Run the full eval loop and return the run summary dict.

    Requires ANTHROPIC_API_KEY in the environment (real LLM calls).
    critic_model overrides the model used for both critic nodes.
    Defaults to MODEL_SONNET so the headline numbers match the production model
    used by the GitHub Action. Pass MODEL_HAIKU for cheap iteration.
    """
    from ai_slop_detector.aggregator import aggregate
    from ai_slop_detector.claude_client import MODEL_SONNET, ClaudeClient
    from ai_slop_detector.graph.pipeline import run_pipeline
    from ai_slop_detector.rag import RAGIndex

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    effective_model = critic_model or MODEL_SONNET
    claude = ClaudeClient(api_key=api_key)
    rag = RAGIndex()

    max_cost_usd: float | None = None
    _cost_env = os.environ.get("MAX_EVAL_COST_USD", "").strip()
    if _cost_env:
        try:
            max_cost_usd = float(_cost_env)
        except ValueError:
            pass

    entries = load_golden_entries(golden_dir)
    if limit:
        entries = entries[:limit]

    skip_critics: set[str] | None = {ablation_critic} if ablation_critic else None

    results = []
    for entry in entries:
        if max_cost_usd is not None and claude.total_cost_usd >= max_cost_usd:
            print(
                f"  Cost limit ${max_cost_usd:.2f} reached "
                f"(spent ${claude.total_cost_usd:.3f}). Stopping early.",
                flush=True,
            )
            break
        if "is_slop" not in entry:
            continue
        is_slop = bool(entry["is_slop"])

        state = entry_to_state(entry)
        if verbose:
            print(f"  Running {entry['repo']} #{entry['pr_number']} (is_slop={is_slop})…")

        try:
            final_state = run_pipeline(state, claude, rag, critic_model=effective_model)
        except Exception as exc:
            if verbose:
                print(f"    ERROR: {exc}")
            results.append({
                "repo": entry["repo"],
                "pr_number": entry["pr_number"],
                "is_slop": is_slop,
                # Errors must NOT flag the PR as slop — the model didn't get to judge.
                # Safe default = approve (no automated action). User sees the error in the run JSON.
                "predicted_decision": "approve",
                "error": str(exc),
            })
            continue

        # For ablation: re-aggregate skipping the ablated critic.
        if skip_critics and final_state.critic_outputs:
            result = aggregate(
                final_state.critic_outputs,
                skip_critics=skip_critics,
            )
            predicted = result.decision
        else:
            predicted = (
                final_state.aggregate_result.decision
                if final_state.aggregate_result
                else (final_state.aggregate_verdict.decision if final_state.aggregate_verdict else "reject")
            )

        results.append({
            "repo": entry["repo"],
            "pr_number": entry["pr_number"],
            "is_slop": is_slop,
            "predicted_decision": predicted,
            "per_critic_scores": (
                final_state.aggregate_result.per_critic_scores
                if final_state.aggregate_result else {}
            ),
        })

    metrics = compute_metrics(results, skip_critics=skip_critics)

    run = {
        "run_at": datetime.now(tz=timezone.utc).isoformat(),
        "golden_dir": str(golden_dir),
        "n_entries": len(results),
        "ablation_critic": ablation_critic,
        "cost_usd": round(claude.total_cost_usd, 4),
        "metrics": metrics,
        "results": results,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = f"_ablate_{ablation_critic}" if ablation_critic else ""
    out_path = out_dir / f"{ts}{suffix}.json"
    out_path.write_text(json.dumps(run, indent=2))

    return run
