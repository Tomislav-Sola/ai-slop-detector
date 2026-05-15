"""Streamlit eval viewer — pr-triage view (Phase 3 / B6).

Launch via:  pr-triage view  [--run path/to/run.json]

Shows:
  - Summary metrics (accuracy, per-class precision/recall/F1)
  - Confusion matrix heatmap
  - Per-critic score distributions (box plots)
  - Disagrement table: cases where predicted != golden label
  - Expandable diff / findings for each disagreement
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

_DEFAULT_RUN_DIR = Path("outputs/eval_runs")

st.set_page_config(page_title="pr-triage eval viewer", layout="wide")
st.title("pr-triage — Eval Run Viewer")


# ------------------------------------------------------------------
# Load run
# ------------------------------------------------------------------

run_path_env = os.environ.get("EVAL_RUN_FILE", "")
if run_path_env and Path(run_path_env).exists():
    run_path = Path(run_path_env)
else:
    runs = sorted(_DEFAULT_RUN_DIR.glob("*.json"), reverse=True) if _DEFAULT_RUN_DIR.exists() else []
    if not runs:
        st.error("No eval runs found. Run `pr-triage eval` first.")
        st.stop()
    run_path = runs[0]

run = json.loads(run_path.read_text())
m = run["metrics"]
results = run["results"]

st.caption(
    f"Run: {run['run_at']}  |  Golden dir: {run['golden_dir']}  |  "
    f"Entries: {run['n_entries']}  |  File: {run_path.name}"
)
if run.get("ablation_critic"):
    st.info(f"Ablation: **{run['ablation_critic']}** excluded from aggregation")


# ------------------------------------------------------------------
# Summary metrics
# ------------------------------------------------------------------

st.header("Summary")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Accuracy", f"{m['accuracy']:.1%}")
col2.metric("Entries", m["n_total"])
col3.metric("Correct", m["n_correct"])
col4.metric("Errors", sum(1 for r in results if "error" in r))

st.subheader("Per-class metrics")
import pandas as pd

pc = m["per_class"]
rows = []
for cls in ["approve", "request_changes", "reject"]:
    s = pc.get(cls, {})
    rows.append({
        "Class": cls,
        "Precision": f"{s.get('precision', 0):.3f}",
        "Recall": f"{s.get('recall', 0):.3f}",
        "F1": f"{s.get('f1', 0):.3f}",
        "TP": s.get("tp", 0),
        "FP": s.get("fp", 0),
        "FN": s.get("fn", 0),
    })
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ------------------------------------------------------------------
# Confusion matrix
# ------------------------------------------------------------------

st.subheader("Confusion matrix")
decisions = ["approve", "request_changes", "reject"]
cm = m.get("confusion_matrix", {})
cm_rows = []
for true_cls in decisions:
    row = {"True \\ Predicted": true_cls}
    for pred_cls in decisions:
        row[pred_cls] = cm.get(true_cls, {}).get(pred_cls, 0)
    cm_rows.append(row)
cm_df = pd.DataFrame(cm_rows).set_index("True \\ Predicted")
st.dataframe(cm_df.style.background_gradient(cmap="Blues"), use_container_width=True)


# ------------------------------------------------------------------
# Per-critic score distributions
# ------------------------------------------------------------------

st.subheader("Per-critic scores")
all_scores: dict[str, list[int]] = {}
for r in results:
    for critic, score in (r.get("per_critic_scores") or {}).items():
        all_scores.setdefault(critic, []).append(score)

if all_scores:
    score_rows = []
    for critic, scores in sorted(all_scores.items()):
        avg = sum(scores) / len(scores)
        score_rows.append({
            "Critic": critic,
            "N": len(scores),
            "Mean": f"{avg:.1f}",
            "Min": min(scores),
            "Max": max(scores),
        })
    st.dataframe(pd.DataFrame(score_rows), use_container_width=True, hide_index=True)

    try:
        import altair as alt
        flat = [
            {"critic": critic, "score": s}
            for critic, scores in all_scores.items()
            for s in scores
        ]
        chart = (
            alt.Chart(pd.DataFrame(flat))
            .mark_boxplot()
            .encode(x="critic:N", y=alt.Y("score:Q", scale=alt.Scale(domain=[0, 10])))
            .properties(height=300)
        )
        st.altair_chart(chart, use_container_width=True)
    except Exception:
        pass  # altair optional
else:
    st.info("No per-critic scores available in this run.")


# ------------------------------------------------------------------
# Disagreements
# ------------------------------------------------------------------

_LABEL_TO_DECISION = {
    "accepted": "approve",
    "rejected_quality": "request_changes",
    "slop": "reject",
}

disagreements = [
    r for r in results
    if _LABEL_TO_DECISION.get(r.get("golden_label", "")) != r.get("predicted_decision")
]

st.header(f"Disagreements ({len(disagreements)} / {len(results)})")

if not disagreements:
    st.success("Perfect agreement on all entries!")
else:
    golden_dir = Path(run.get("golden_dir", "tests/fixtures/golden"))

    for r in disagreements:
        true_dec = _LABEL_TO_DECISION.get(r.get("golden_label", ""), "?")
        pred_dec = r.get("predicted_decision", "?")
        label = f"{r['repo']} #{r['pr_number']}  |  Golden: **{r['golden_label']}** → {true_dec}  |  Predicted: **{pred_dec}**"
        with st.expander(label):
            safe = r["repo"].replace("/", "__")
            fixture_path = golden_dir / f"{safe}_pr{r['pr_number']}.json"
            if fixture_path.exists():
                entry = json.loads(fixture_path.read_text())
                st.write(f"**{entry.get('title', '')}**")
                st.caption(
                    f"author={entry.get('author')}  |  "
                    f"assoc={entry.get('author_association')}  |  "
                    f"+{entry.get('additions', 0)}/-{entry.get('deletions', 0)}  |  "
                    f"merged={entry.get('merged')}"
                )
                if entry.get("label_notes"):
                    st.info(f"Label notes: {entry['label_notes']}")

                scores = r.get("per_critic_scores", {})
                if scores:
                    st.write("**Critic scores:**", scores)

                diff = entry.get("raw_diff", "")
                if diff:
                    with st.expander("Diff (first 200 lines)"):
                        lines = diff.splitlines()[:200]
                        st.code("\n".join(lines), language="diff")
            else:
                st.warning("Fixture file not found locally.")
            if r.get("error"):
                st.error(f"Pipeline error: {r['error']}")
