"""Eval harness for the multi-critic triage pipeline (Phase 3 / B5).

Usage:
    pr-triage eval [--ablation critic_name] [--limit N] [--golden-dir PATH]

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

from pr_triage.state import (
    CriticOutput,
    PRMetadata,
    TriageState,
)

_GOLDEN_DIR = Path("tests/fixtures/golden")
_OUTPUT_DIR = Path("outputs/eval_runs")

# Maps golden label → expected pipeline decision.
_LABEL_TO_DECISION: dict[str, str] = {
    "accepted": "approve",
    "rejected_quality": "request_changes",
    "slop": "reject",
}

# All valid decision classes for the confusion matrix.
_DECISIONS = ["approve", "request_changes", "reject"]


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
    meta = PRMetadata(
        number=entry["pr_number"],
        title=entry["title"],
        body=entry.get("body"),
        author=entry.get("author", "unknown"),
        created_at=_parse_dt(entry.get("created_at")),
        updated_at=_parse_dt(entry.get("updated_at")),
        base_branch=entry.get("base_branch", "main"),
        head_branch=entry.get("head_branch", "patch"),
        additions=entry.get("additions", 0),
        deletions=entry.get("deletions", 0),
        changed_files=entry.get("changed_files", 0),
        labels=entry.get("labels", []),
        draft=entry.get("draft", False),
        merged=entry.get("merged", False),
    )

    files_changed: list[str] = []
    for f in (entry.get("files_changed") or []):
        if isinstance(f, dict):
            files_changed.append(f.get("filename", ""))
        else:
            files_changed.append(str(f))

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
    """Compute precision, recall, F1, and confusion matrix.

    Each result dict must have:
      - golden_label: str
      - predicted_decision: str
    """
    confusion: dict[str, dict[str, int]] = {
        true: {pred: 0 for pred in _DECISIONS}
        for true in _DECISIONS
    }

    for r in results:
        true_dec = _LABEL_TO_DECISION.get(r["golden_label"], "request_changes")
        pred_dec = r.get("predicted_decision", "request_changes")
        if true_dec in confusion and pred_dec in confusion[true_dec]:
            confusion[true_dec][pred_dec] += 1

    per_class: dict[str, dict] = {}
    for cls in _DECISIONS:
        tp = confusion[cls][cls]
        fp = sum(confusion[other][cls] for other in _DECISIONS if other != cls)
        fn = sum(confusion[cls][other] for other in _DECISIONS if other != cls)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)
        per_class[cls] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn,
        }

    total = len(results)
    correct = sum(
        1 for r in results
        if _LABEL_TO_DECISION.get(r["golden_label"]) == r.get("predicted_decision")
    )
    accuracy = correct / total if total > 0 else 0.0

    return {
        "accuracy": round(accuracy, 3),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "n_total": total,
        "n_correct": correct,
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
    critic_model overrides the model used for all three critic nodes.
    Defaults to MODEL_HAIKU to keep eval costs low.
    """
    from pr_triage.aggregator import aggregate
    from pr_triage.claude_client import MODEL_HAIKU, ClaudeClient
    from pr_triage.graph.pipeline import run_pipeline
    from pr_triage.rag import RAGIndex

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    effective_model = critic_model or MODEL_HAIKU
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
        label = entry.get("golden_label", "")
        if label not in _LABEL_TO_DECISION:
            continue

        state = entry_to_state(entry)
        if verbose:
            print(f"  Running {entry['repo']} #{entry['pr_number']} (label={label})…")

        try:
            final_state = run_pipeline(state, claude, rag, critic_model=effective_model)
        except Exception as exc:
            if verbose:
                print(f"    ERROR: {exc}")
            results.append({
                "repo": entry["repo"],
                "pr_number": entry["pr_number"],
                "golden_label": label,
                "predicted_decision": "request_changes",
                "error": str(exc),
            })
            continue

        # For ablation: re-aggregate skipping the ablated critic.
        if skip_critics and final_state.critic_outputs:
            result = aggregate(final_state.critic_outputs, skip_critics=skip_critics)
            predicted = result.decision
        else:
            predicted = (
                final_state.aggregate_result.decision
                if final_state.aggregate_result
                else (final_state.aggregate_verdict.decision if final_state.aggregate_verdict else "request_changes")
            )

        results.append({
            "repo": entry["repo"],
            "pr_number": entry["pr_number"],
            "golden_label": label,
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
