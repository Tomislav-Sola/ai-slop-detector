"""Streamlit manual labeling tool for golden set construction.

Launch via `pr-triage label` or directly:
    streamlit run src/pr_triage/labeler_app.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

_PRE_LABELS_PATH = Path(os.environ.get("LABELER_PRE_LABELS", "data/pre_labels.jsonl"))
_CANDIDATES_DIR = Path(os.environ.get("LABELER_CANDIDATES_DIR", "data/candidates"))
_GOLDEN_PATH = Path(os.environ.get("LABELER_OUT", "data/golden_labels.jsonl"))

_CONF_RANK = {"unclear": 0, "low": 1, "medium": 2, "high": 3}
_PRE_TO_GOLDEN: dict[str, str] = {
    "accepted": "approve",
    "rejected_quality": "request_changes",
    "slop": "reject",
}
_SIGNAL_LABELS = {
    "ai_disclosure_or_mention": "🤖 ai_disclosure_or_mention",
    "silent_slop_pattern": "🔇 silent_slop_pattern",
    "maintainer_explicit_rejection": "🚫 maintainer_explicit_rejection",
}
_GOLDEN_EMOJI = {"approve": "✅", "request_changes": "🔄", "reject": "🗑️"}
_MAX_DIFF_LINES = 500


# ------------------------------------------------------------------
# Data I/O
# ------------------------------------------------------------------

def _load_golden() -> dict[tuple[str, int], str]:
    out: dict[tuple[str, int], str] = {}
    if _GOLDEN_PATH.exists():
        for line in _GOLDEN_PATH.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                out[(e["repo"], e["pr_number"])] = e["label"]
    return out


def _build_queue(labeled: set[tuple[str, int]]) -> list[dict]:
    entries: list[dict] = []
    if not _PRE_LABELS_PATH.exists():
        return entries
    for line in _PRE_LABELS_PATH.read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            if (e["repo"], e["pr_number"]) not in labeled:
                entries.append(e)

    def _rank(e: dict) -> int:
        if e["label"] == "unclear":
            return 0
        return _CONF_RANK.get(e.get("confidence", "low"), 1)

    entries.sort(key=_rank)
    return entries


def _load_candidate(entry: dict) -> dict:
    safe = entry["repo"].replace("/", "__")
    path = _CANDIDATES_DIR / f"{safe}_pr{entry['pr_number']}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _count_labels() -> dict[str, int]:
    counts: dict[str, int] = {"approve": 0, "request_changes": 0, "reject": 0}
    if _GOLDEN_PATH.exists():
        for line in _GOLDEN_PATH.read_text().splitlines():
            if line.strip():
                lbl = json.loads(line).get("label", "")
                if lbl in counts:
                    counts[lbl] += 1
    return counts


def _save_label(repo: str, pr_number: int, label: str, notes: str = "") -> None:
    _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {"repo": repo, "pr_number": pr_number, "label": label}
    if notes:
        entry["notes"] = notes
    with _GOLDEN_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ------------------------------------------------------------------
# Session state
# ------------------------------------------------------------------

def _init() -> None:
    if "initialized" in st.session_state:
        return
    labeled = _load_golden()
    st.session_state.labeled = labeled
    st.session_state.queue = _build_queue(set(labeled.keys()))
    st.session_state.idx = 0
    st.session_state.counts = _count_labels()
    st.session_state.initialized = True


def _do_label(repo: str, pr_number: int, golden_label: str, notes: str = "") -> None:
    _save_label(repo, pr_number, golden_label, notes)
    st.session_state.labeled[(repo, pr_number)] = golden_label
    if golden_label in st.session_state.counts:
        st.session_state.counts[golden_label] += 1
    st.session_state.idx += 1


def _do_skip() -> None:
    q = st.session_state.queue
    i = st.session_state.idx
    item = q[i]
    st.session_state.queue = q[:i] + q[i + 1:] + [item]


# ------------------------------------------------------------------
# UI helpers
# ------------------------------------------------------------------

def _keyboard_js() -> None:
    components.html("""
<script>
(function() {
    const doc = window.parent.document;
    if (doc.__kbReady) return;
    doc.__kbReady = true;
    doc.addEventListener('keydown', function(e) {
        if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const map = {
            'a': 'Accept suggestion',
            'r': 'reject',
            's': 'Skip',
        };
        const needle = map[e.key.toLowerCase()];
        if (!needle) return;
        const btns = doc.querySelectorAll('button');
        for (const btn of btns) {
            if (btn.innerText.includes(needle)) {
                e.preventDefault();
                btn.click();
                return;
            }
        }
    });
})();
</script>
""", height=0)


def _render_diff(raw_diff: str) -> None:
    if not raw_diff:
        st.caption("No diff available.")
        return
    lines = raw_diff.splitlines()
    total = len(lines)
    if total <= _MAX_DIFF_LINES:
        st.code(raw_diff, language="diff")
        return
    st.code("\n".join(lines[:_MAX_DIFF_LINES]), language="diff")
    st.caption(f"Showing first {_MAX_DIFF_LINES} of {total} lines.")
    with st.expander(f"Show all {total} lines"):
        st.code(raw_diff, language="diff")


def _render_comments(candidate: dict) -> None:
    ic = candidate.get("issue_comments", [])
    rc = candidate.get("review_comments", [])
    all_c = [("issue", c) for c in ic] + [("review", c) for c in rc]
    if not all_c:
        st.caption("No comments.")
        return
    for kind, c in all_c:
        assoc = c.get("author_association", "NONE")
        user = c.get("user", "?")
        body = (c.get("body") or "")[:800]
        created = (c.get("created_at") or "")[:10]
        tag = f"[{assoc}]" if assoc not in ("NONE", "") else ""
        st.markdown(f"**{user}** {tag} · *{kind}* · {created}")
        st.markdown(body + ("…" if len(c.get("body") or "") > 800 else ""))
        st.divider()


def _prelabel_badge(pre_label: str, confidence: str) -> str:
    icons = {"accepted": "🟢", "rejected_quality": "🟠", "slop": "🔴", "unclear": "🟡"}
    icon = icons.get(pre_label, "⚪")
    if pre_label == "unclear":
        return f"{icon} **unclear** — no confident signal"
    return f"{icon} **{pre_label}** ({confidence} confidence)"


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

st.set_page_config(page_title="PR Labeler", layout="wide", page_icon="🏷️")
_init()
_keyboard_js()

queue = st.session_state.queue
idx = st.session_state.idx
counts = st.session_state.counts
total = len(queue)

# Done screen
if idx >= total:
    st.success(f"Queue complete — {sum(counts.values())} PRs labeled.")
    c1, c2, c3 = st.columns(3)
    c1.metric("✅ approve", counts["approve"])
    c2.metric("🔄 request_changes", counts["request_changes"])
    c3.metric("🗑️ reject", counts["reject"])
    st.info("Run `pr-triage golden-build` to write the golden fixtures.")
    if st.button("Restart (re-review skipped PRs)"):
        del st.session_state["initialized"]
        st.rerun()
    st.stop()

entry = queue[idx]
repo = entry["repo"]
pr_number = entry["pr_number"]
pre_label = entry["label"]
confidence = entry.get("confidence", "low")
signals = entry.get("signals", [])
suggested_golden = _PRE_TO_GOLDEN.get(pre_label)

candidate = _load_candidate(entry)

# Progress bar
st.progress(idx / max(total, 1), text=f"PR {idx + 1} / {total}  ·  labeled: {sum(counts.values())}")

# Title / link
pr_url = f"https://github.com/{repo}/pull/{pr_number}"
title = candidate.get("title") or entry.get("title", "")
st.markdown(f"## [{repo} #{pr_number}]({pr_url})")
st.markdown(f"### {title}")

left, right = st.columns([2, 1])

with left:
    additions = candidate.get("additions", entry.get("additions", 0))
    deletions = candidate.get("deletions", entry.get("deletions", 0))
    changed = candidate.get("changed_files", entry.get("changed_files_count", "?"))
    closed_at = (candidate.get("closed_at") or "")[:10]
    merged = candidate.get("merged", False)
    author = candidate.get("author", "?")
    prior_prs = candidate.get("author_prior_prs_in_repo")
    gh_labels = ", ".join(candidate.get("labels", [])) or "—"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Author", author)
    m2.metric("Diff", f"+{additions} / -{deletions}")
    m3.metric("Files", changed)
    m4.metric("Closed", closed_at)
    st.caption(
        f"Prior PRs in repo: {prior_prs if prior_prs is not None else 'unknown'}"
        f"  ·  Merged: {merged}  ·  Labels: {gh_labels}"
    )
    st.divider()

    body = candidate.get("body") or ""
    with st.expander("PR Body", expanded=bool(body and len(body) < 2000)):
        if body:
            st.markdown(body[:4000] + ("…" if len(body) > 4000 else ""))
        else:
            st.caption("No body.")

    with st.expander(f"Diff  (+{additions} / -{deletions})", expanded=True):
        _render_diff(candidate.get("raw_diff", ""))

    ic_count = len(candidate.get("issue_comments", []))
    rc_count = len(candidate.get("review_comments", []))
    with st.expander(f"Comments  ({ic_count} issue · {rc_count} review)", expanded=bool(signals)):
        _render_comments(candidate)

with right:
    st.markdown("### Suggested label")
    st.markdown(_prelabel_badge(pre_label, confidence))

    if signals:
        st.markdown("**Signals matched:**")
        for sig in signals:
            st.markdown(f"- {_SIGNAL_LABELS.get(sig, sig)}")
    else:
        st.caption("No slop signals.")

    st.divider()

    notes = st.text_input(
        "Notes (optional)",
        key=f"notes_{idx}",
        placeholder="reason for override, edge case observation…",
    )

    st.markdown("**Actions** · `a` accept · `r` reject · `s` skip")

    if suggested_golden:
        emoji = _GOLDEN_EMOJI.get(suggested_golden, "")
        if st.button(
            f"Accept suggestion  →  {emoji} {suggested_golden}",
            type="primary",
            use_container_width=True,
        ):
            _do_label(repo, pr_number, suggested_golden, notes)
            st.rerun()

    st.markdown("**Override:**")
    ov1, ov2, ov3 = st.columns(3)
    with ov1:
        if st.button("✅ approve", use_container_width=True, key="btn_approve"):
            _do_label(repo, pr_number, "approve", notes)
            st.rerun()
    with ov2:
        if st.button("🔄 request_changes", use_container_width=True, key="btn_rc"):
            _do_label(repo, pr_number, "request_changes", notes)
            st.rerun()
    with ov3:
        if st.button("🗑️ reject", use_container_width=True, key="btn_reject"):
            _do_label(repo, pr_number, "reject", notes)
            st.rerun()

    st.divider()

    if st.button("Skip →", use_container_width=True):
        _do_skip()
        st.rerun()

# Footer — running counts
st.divider()
f1, f2, f3, f4, f5 = st.columns(5)
f1.metric("✅ approve", counts["approve"], delta=f"target 20, {max(20 - counts['approve'], 0)} left")
f2.metric("🔄 request_changes", counts["request_changes"], delta=f"target 20, {max(20 - counts['request_changes'], 0)} left")
f3.metric("🗑️ reject", counts["reject"], delta=f"target 10, {max(10 - counts['reject'], 0)} left")
f4.metric("Total labeled", sum(counts.values()))
f5.metric("Queue remaining", total - idx)
