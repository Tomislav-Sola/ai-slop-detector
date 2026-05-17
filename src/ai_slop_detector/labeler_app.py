"""Streamlit manual labeling tool for golden set construction.

Launch via `ai-slop-detector label` or directly:
    streamlit run src/ai_slop_detector/labeler_app.py

Binary labeling: each PR is is_slop=True or False. The labeler shows the
prelabel suggestion and lets the human accept or override with one click.
Output is appended to data/golden_labels.jsonl as
{"repo": ..., "pr_number": ..., "is_slop": bool}.
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
_QUEUE_MODE = os.environ.get("LABELER_QUEUE_MODE", "slop-first")
_SKIP_MAINTAINER = os.environ.get("LABELER_SKIP_MAINTAINER", "0") == "1"

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}
_SIGNAL_LABELS = {
    "ai_disclosure_or_mention": "🤖 ai_disclosure_or_mention",
    "silent_slop_pattern": "🔇 silent_slop_pattern",
    "maintainer_explicit_rejection": "🚫 maintainer_explicit_rejection",
}
_MAX_DIFF_LINES = 500
_MAINTAINER_PRIOR_PRS_THRESHOLD = 50


# ------------------------------------------------------------------
# Data I/O
# ------------------------------------------------------------------

def _load_golden() -> dict[tuple[str, int], bool | None]:
    """Return labelled entries keyed by (repo, pr_number) → is_slop (or None for skip)."""
    out: dict[tuple[str, int], bool | None] = {}
    if _GOLDEN_PATH.exists():
        for line in _GOLDEN_PATH.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                key = (e["repo"], e["pr_number"])
                if e.get("skip"):
                    out[key] = None
                else:
                    out[key] = bool(e.get("is_slop", False))
    return out


def _load_candidate(entry: dict) -> dict:
    safe = entry["repo"].replace("/", "__")
    path = _CANDIDATES_DIR / f"{safe}_pr{entry['pr_number']}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _count_labels() -> dict[str, int]:
    """Count slop / not-slop / skip across the labels file."""
    counts = {"slop": 0, "not_slop": 0, "skip": 0}
    if _GOLDEN_PATH.exists():
        for line in _GOLDEN_PATH.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                if e.get("skip"):
                    counts["skip"] += 1
                elif e.get("is_slop"):
                    counts["slop"] += 1
                else:
                    counts["not_slop"] += 1
    return counts


def _save_label(repo: str, pr_number: int, is_slop: bool | None, *, notes: str = "") -> None:
    """Append a binary label decision (or skip) to the output file."""
    _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {"repo": repo, "pr_number": pr_number}
    if is_slop is None:
        entry["skip"] = True
    else:
        entry["is_slop"] = is_slop
    if notes:
        entry["notes"] = notes
    with _GOLDEN_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()


# ------------------------------------------------------------------
# Queue construction
# ------------------------------------------------------------------

def _load_pre_labels(labeled: set[tuple[str, int]]) -> list[dict]:
    entries: list[dict] = []
    if not _PRE_LABELS_PATH.exists():
        return entries
    for line in _PRE_LABELS_PATH.read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            if (e["repo"], e["pr_number"]) not in labeled:
                entries.append(e)
    return entries


def _sort_slop_first(entries: list[dict]) -> list[dict]:
    """Likely-slop first (by signal count desc), then uncertain not-slop, then confident not-slop."""
    slop = [e for e in entries if e.get("is_slop_likely")]
    slop.sort(key=lambda e: (
        -len(e.get("signals", [])),
        -_CONF_RANK.get(e.get("confidence", "low"), 0),
    ))
    not_slop = [e for e in entries if not e.get("is_slop_likely")]
    # Uncertain (low confidence) first so the human reviews ambiguous cases earlier.
    not_slop.sort(key=lambda e: _CONF_RANK.get(e.get("confidence", "low"), 0))
    return slop + not_slop


def _sort_confidence_asc(entries: list[dict]) -> list[dict]:
    """Hardest first — ambiguous calls (low confidence) before confident ones."""
    return sorted(entries, key=lambda e: _CONF_RANK.get(e.get("confidence", "low"), 0))


def _build_queue(entries: list[dict]) -> list[dict]:
    mode = _QUEUE_MODE
    if mode == "is_slop=true":
        return [e for e in entries if e.get("is_slop_likely")]
    if mode == "is_slop=false":
        return [e for e in entries if not e.get("is_slop_likely")]
    if mode == "confidence-asc":
        return _sort_confidence_asc(entries)
    return _sort_slop_first(entries)  # default: slop-first


# ------------------------------------------------------------------
# Maintainer cleanup auto-skip
# ------------------------------------------------------------------

_MAINTAINER_ASSOC = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def _is_maintainer_cleanup(candidate: dict) -> bool:
    prior = candidate.get("author_prior_prs_in_repo")
    assoc = candidate.get("author_association")
    is_maintainer = (assoc in _MAINTAINER_ASSOC) or (
        prior is not None and prior >= _MAINTAINER_PRIOR_PRS_THRESHOLD
    )
    return (
        not candidate.get("merged", True)
        and is_maintainer
        and not candidate.get("issue_comments")
        and not candidate.get("review_comments")
    )


def _auto_skip_maintainer_cleanups(
    queue: list[dict], labeled: dict[tuple[str, int], bool | None]
) -> tuple[list[dict], int]:
    remaining: list[dict] = []
    auto_skipped = 0
    for entry in queue:
        candidate = _load_candidate(entry)
        # Never auto-skip slop-flagged PRs — those need human verification.
        if _is_maintainer_cleanup(candidate) and not entry.get("is_slop_likely"):
            key = (entry["repo"], entry["pr_number"])
            _save_label(entry["repo"], entry["pr_number"], None, notes="maintainer_cleanup_auto_skip")
            labeled[key] = None
            auto_skipped += 1
        else:
            remaining.append(entry)
    return remaining, auto_skipped


# ------------------------------------------------------------------
# Session state
# ------------------------------------------------------------------

def _total_pre_labels() -> int:
    if not _PRE_LABELS_PATH.exists():
        return 0
    return sum(1 for line in _PRE_LABELS_PATH.read_text().splitlines() if line.strip())


def _init() -> None:
    if "initialized" in st.session_state:
        return
    labeled = _load_golden()
    total_pre = _total_pre_labels()
    already_labeled = len(labeled)
    entries = _load_pre_labels(set(labeled.keys()))
    queue = _build_queue(entries)
    auto_skipped = 0
    if _SKIP_MAINTAINER:
        queue, auto_skipped = _auto_skip_maintainer_cleanups(queue, labeled)
    first = queue[0] if queue else None
    if first:
        startup_msg = (
            f"{already_labeled} of {total_pre} already labeled, "
            f"resuming at {first['repo']} #{first['pr_number']} "
            f"(next unlabeled in {_QUEUE_MODE} queue)"
        )
    else:
        startup_msg = f"{already_labeled} of {total_pre} already labeled — queue complete!"
    st.session_state.labeled = labeled
    st.session_state.queue = queue
    st.session_state.idx = 0
    st.session_state.counts = _count_labels()
    st.session_state.auto_skipped = auto_skipped
    st.session_state.startup_msg = startup_msg
    st.session_state.disk_writes = already_labeled
    st.session_state.initialized = True


def _do_label(repo: str, pr_number: int, is_slop: bool, notes: str = "") -> None:
    _save_label(repo, pr_number, is_slop, notes=notes)
    st.session_state.labeled[(repo, pr_number)] = is_slop
    if is_slop:
        st.session_state.counts["slop"] += 1
    else:
        st.session_state.counts["not_slop"] += 1
    st.session_state.idx += 1
    st.session_state.disk_writes += 1


def _do_skip(notes: str = "") -> None:
    q = st.session_state.queue
    i = st.session_state.idx
    entry = q[i]
    _save_label(entry["repo"], entry["pr_number"], None, notes=notes)
    st.session_state.labeled[(entry["repo"], entry["pr_number"])] = None
    st.session_state.counts["skip"] += 1
    st.session_state.idx += 1
    st.session_state.disk_writes += 1


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
            'n': 'Not slop',
            'r': 'Slop',
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
        body = c.get("body") or ""
        created = (c.get("created_at") or "")[:10]
        tag = f"[{assoc}]" if assoc not in ("NONE", "") else ""
        st.markdown(f"**{user}** {tag} · *{kind}* · {created}")
        st.markdown(body[:800] + ("…" if len(body) > 800 else ""))
        st.divider()


def _prelabel_badge(is_slop_likely: bool, confidence: str, has_signals: bool) -> str:
    if is_slop_likely:
        return f"🗑️ Suggested: **Slop** ({confidence} confidence)"
    if not has_signals and confidence == "low":
        return "🟡 Suggested: **Not slop** (low confidence — no engagement)"
    return f"✅ Suggested: **Not slop** ({confidence} confidence)"


def _progress_text(idx: int, total: int, counts: dict) -> str:
    return (
        f"PR {idx + 1}/{total} in queue  |  labeled: "
        f"is_slop=True: {counts['slop']}  ·  is_slop=False: {counts['not_slop']}  ·  skip: {counts['skip']}"
    )


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
auto_skipped = st.session_state.auto_skipped

st.info(st.session_state.startup_msg)
st.caption(f"Persisted to disk: {st.session_state.disk_writes} labels  ·  Output: `{_GOLDEN_PATH}`")

if auto_skipped:
    st.info(f"Auto-skipped {auto_skipped} maintainer cleanup PRs. Written as skip to output.")

# Done screen
if idx >= total:
    st.success(f"Queue complete — labeled {counts['slop'] + counts['not_slop']} PRs ({counts['skip']} skipped).")
    c1, c2, c3 = st.columns(3)
    c1.metric("🗑️ is_slop=True", counts["slop"])
    c2.metric("✅ is_slop=False", counts["not_slop"])
    c3.metric("⏭️ skip", counts["skip"])
    st.info("Run `ai-slop-detector golden-build` to write the golden fixtures.")
    if st.button("Restart (re-review skipped PRs)"):
        del st.session_state["initialized"]
        st.rerun()
    st.stop()

entry = queue[idx]
repo = entry["repo"]
pr_number = entry["pr_number"]
is_slop_likely = bool(entry.get("is_slop_likely", False))
confidence = entry.get("confidence", "low")
signals = entry.get("signals", [])

candidate = _load_candidate(entry)

# Progress bar
st.progress(
    idx / max(total, 1),
    text=_progress_text(idx, total, counts),
)

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
    assoc = candidate.get("author_association") or "unknown"
    st.caption(
        f"Prior PRs in repo: {prior_prs if prior_prs is not None else 'unknown'}"
        f"  ·  Association: {assoc}"
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
    st.markdown("### Suggestion")
    st.markdown(_prelabel_badge(is_slop_likely, confidence, bool(signals)))

    if signals:
        st.markdown("**Signals matched:**")
        for sig in signals:
            st.markdown(f"- {_SIGNAL_LABELS.get(sig, sig)}")
    else:
        st.caption("No slop signals detected.")

    st.divider()

    notes = st.text_input(
        "Notes (optional)",
        key=f"notes_{idx}",
        placeholder="reason for override, edge case…",
    )

    st.markdown("**Actions** · `a` accept · `r` Slop · `n` Not slop · `s` skip")

    accept_label = (
        "Accept suggestion  →  🗑️ Slop"
        if is_slop_likely
        else "Accept suggestion  →  ✅ Not slop"
    )
    if st.button(accept_label, type="primary", use_container_width=True):
        _do_label(repo, pr_number, is_slop_likely, notes)
        st.rerun()

    st.markdown("**Override:**")
    ov1, ov2 = st.columns(2)
    with ov1:
        if st.button("🗑️ Slop", use_container_width=True, key="btn_slop"):
            _do_label(repo, pr_number, True, notes)
            st.rerun()
    with ov2:
        if st.button("✅ Not slop", use_container_width=True, key="btn_not_slop"):
            _do_label(repo, pr_number, False, notes)
            st.rerun()

    st.divider()

    if st.button("Skip →", use_container_width=True):
        _do_skip(notes)
        st.rerun()

# Footer — binary counts only.
st.divider()
f1, f2, f3, f4 = st.columns(4)
f1.metric("🗑️ is_slop=True", counts["slop"],
          delta=f"target 10, {max(10 - counts['slop'], 0)} left")
f2.metric("✅ is_slop=False", counts["not_slop"],
          delta=f"target 40, {max(40 - counts['not_slop'], 0)} left")
f3.metric("⏭️ skip", counts["skip"])
f4.metric("Queue remaining", total - idx)
