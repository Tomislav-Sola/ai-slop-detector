"""Streamlit eval viewer — ai-slop-detector view.

Launch via:  ai-slop-detector view  [--run path/to/run.json]

Binary slop classifier view: summary metrics on the slop class (precision,
recall, F1), 2x2 confusion matrix, and a disagreements table split into
false positives and false negatives.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

_DEFAULT_RUN_DIR = Path("outputs/eval_runs")

st.set_page_config(page_title="ai-slop-detector eval viewer", layout="wide")
st.title("ai-slop-detector — Eval Run Viewer")


# ------------------------------------------------------------------
# Load run
# ------------------------------------------------------------------

run_path_env = os.environ.get("EVAL_RUN_FILE", "")
if run_path_env and Path(run_path_env).exists():
    run_path = Path(run_path_env)
else:
    runs = sorted(_DEFAULT_RUN_DIR.glob("*.json"), reverse=True) if _DEFAULT_RUN_DIR.exists() else []
    if not runs:
        st.error("No eval runs found. Run `ai-slop-detector eval` first.")
        st.stop()
    run_path = runs[0]

run = json.loads(run_path.read_text())
m = run["metrics"]
results = run["results"]


def _result_is_slop(r: dict) -> bool:
    """Read is_slop from a result row."""
    return bool(r.get("is_slop", False))


st.caption(
    f"Run: {run['run_at']}  |  Golden dir: {run['golden_dir']}  |  "
    f"Entries: {run['n_entries']}  |  File: {run_path.name}"
)
if run.get("ablation_critic"):
    st.info(f"Ablation: **{run['ablation_critic']}** excluded from aggregation")
if run.get("archived_post_hoc_only"):
    archived = ", ".join(run["archived_post_hoc_only"])
    st.caption(f"Note: post-hoc-only slop fixtures archived from this run: {archived}")


# ------------------------------------------------------------------
# Summary metrics — slop class is the focus
# ------------------------------------------------------------------

st.header("Summary — slop detection")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Slop precision", f"{m.get('slop_precision', 0):.3f}")
col2.metric("Slop recall", f"{m.get('slop_recall', 0):.3f}")
col3.metric("Slop F1", f"{m.get('slop_f1', 0):.3f}")
col4.metric("Accuracy", f"{m.get('accuracy', 0):.1%}")

col5, col6, col7, col8 = st.columns(4)
col5.metric("TP (correct slop)", m.get("tp", 0))
col6.metric("FP (false alarms)", m.get("fp", 0))
col7.metric("FN (missed slop)", m.get("fn", 0))
col8.metric("TN (correct approve)", m.get("tn", 0))

errored = [r for r in results if r.get("error")]
if errored:
    with st.expander(f"⚠️  {len(errored)} PR(s) could not be evaluated — defaulted to approve"):
        st.caption(
            "These PRs did not produce a model verdict. The safe default is approve "
            "(no automated slop flag) so a maintainer is not surprised by an "
            "unjustified label. Common causes: pre-flight token-budget cap on very "
            "large diffs, transient API errors, malformed JSON output."
        )
        for r in errored:
            st.write(
                f"- **{r['repo']} #{r['pr_number']}** "
                f"(is_slop={r.get('is_slop', '?')}): "
                f"`{r.get('error', '')[:200]}`"
            )

import pandas as pd


# ------------------------------------------------------------------
# Binary confusion matrix
# ------------------------------------------------------------------

st.subheader("Binary confusion matrix")
decisions = ["approve", "reject"]
cm = m.get("confusion_matrix", {})
cm_rows = []
for true_cls in decisions:
    row = {"True is_slop \\ Predicted": ("slop" if true_cls == "reject" else "not-slop")}
    for pred_cls in decisions:
        row[("slop" if pred_cls == "reject" else "not-slop")] = (
            cm.get(true_cls, {}).get(pred_cls, 0)
        )
    cm_rows.append(row)
cm_df = pd.DataFrame(cm_rows).set_index("True is_slop \\ Predicted")
st.dataframe(cm_df, use_container_width=True)


# ------------------------------------------------------------------
# Disagreements — split into false positives (worse for trust) and false negatives.
# ------------------------------------------------------------------

def _expected_decision(r: dict) -> str:
    return "reject" if _result_is_slop(r) else "approve"


false_positives = [r for r in results if _expected_decision(r) == "approve" and r.get("predicted_decision") == "reject"]
false_negatives = [r for r in results if _expected_decision(r) == "reject" and r.get("predicted_decision") == "approve"]

st.header(f"Disagreements — {len(false_positives)} FP + {len(false_negatives)} FN")

with st.expander("Score legend"):
    st.markdown(
        """
**Critic scores (0–10, per critic):**

| Score | Meaning |
|---|---|
| **10** | Exemplary contribution — clear intent, good engineering hygiene |
| **8** | Solid — common case for legitimate PRs |
| **6** | Neutral / borderline |
| **4** | Significant slop markers — vague description, generic AI phrases, or one strong negative signal |
| **2** | Clear slop — a hard cap fired (AI-disclosure footer, drive-by overreach, sibling-repo mismatch, manipulative @-mention, AI-checklist theatre) |
| **0** | Pure boilerplate / wrong-target |

**Aggregator decision:**
- Weighted score = `0.4 × architecture_critic + 0.6 × slop_signals_critic`
- **Score ≥ 5.0** → `approve` (not slop)
- **Score < 5.0** → `reject` (slop)
- **Veto rule:** any critic ≤ **3** caps the overall score at **3** → automatic reject. One strong slop signal from either critic alone forces a slop verdict.
        """
    )

if not false_positives and not false_negatives:
    st.success("Perfect agreement on all entries.")


golden_dir = Path(run.get("golden_dir", "tests/fixtures/golden"))


def _render_entry(r: dict, kind: str) -> None:
    pred = r.get("predicted_decision", "?")
    is_slop = _result_is_slop(r)
    header = (
        f"{r['repo']} #{r['pr_number']}  |  "
        f"is_slop=**{is_slop}**  |  Predicted: **{pred}**"
    )
    with st.expander(header):
        safe = r["repo"].replace("/", "__")
        fixture_path = golden_dir / f"{safe}_pr{r['pr_number']}.json"
        if fixture_path.exists():
            entry = json.loads(fixture_path.read_text())
            st.write(f"**{entry.get('title', '')}**")
            st.caption(
                f"author={entry.get('author')}  |  "
                f"assoc={entry.get('author_association')}  |  "
                f"prior_prs={entry.get('author_prior_prs_in_repo')}  |  "
                f"+{entry.get('additions', 0)}/-{entry.get('deletions', 0)}"
            )

            scores = r.get("per_critic_scores", {})
            if scores:
                st.write("**Critic scores:**", scores)

            diff = entry.get("raw_diff", "")
            if diff:
                with st.expander("Diff (first 200 lines)"):
                    lines = diff.splitlines()[:200]
                    st.code("\n".join(lines), language="diff")
        else:
            st.warning(f"Fixture file not found locally at {fixture_path}.")
        if r.get("error"):
            st.error(f"Pipeline error: {r['error']}")


if false_positives:
    st.subheader(f"False positives — not-slop PRs flagged as slop ({len(false_positives)})")
    st.caption("These hurt user trust the most. Investigate the critic scores to understand the model's reasoning.")
    for r in false_positives:
        _render_entry(r, "fp")

if false_negatives:
    st.subheader(f"False negatives — slop PRs missed ({len(false_negatives)})")
    st.caption("Each represents slop reaching the maintainer without a warning label.")
    for r in false_negatives:
        _render_entry(r, "fn")
