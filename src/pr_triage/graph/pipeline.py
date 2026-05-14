from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph

from pr_triage import budget as _budget
from pr_triage.budget import BudgetExceeded
from pr_triage.graph.nodes import (
    classify_size_node,
    emit_verdict_node,
    guidelines_critic_node,
    ingest_pr_node,
    retrieve_context_node,
)
from pr_triage.state import TriageState

if TYPE_CHECKING:
    from pr_triage.claude_client import ClaudeClient
    from pr_triage.rag import RAGIndex

_DEFAULT_MAX_TOKENS = 50_000


def build_graph(claude: ClaudeClient, rag: RAGIndex):
    """Assemble and compile the triage StateGraph.

    Nodes are bound to their dependencies (claude, rag) via closures.
    """
    graph = StateGraph(TriageState)

    graph.add_node("ingest_pr", ingest_pr_node)
    graph.add_node("classify_size", lambda s: classify_size_node(s, claude))
    graph.add_node("retrieve_context", lambda s: retrieve_context_node(s, rag))
    graph.add_node("guidelines_critic", lambda s: guidelines_critic_node(s, claude))
    graph.add_node("emit_verdict", emit_verdict_node)

    graph.set_entry_point("ingest_pr")
    graph.add_edge("ingest_pr", "classify_size")
    graph.add_conditional_edges(
        "classify_size",
        lambda s: "skip_critic" if s.size_classification == "trivial" else "run_critic",
        {"skip_critic": "emit_verdict", "run_critic": "retrieve_context"},
    )
    graph.add_edge("retrieve_context", "guidelines_critic")
    graph.add_edge("guidelines_critic", "emit_verdict")
    graph.add_edge("emit_verdict", END)

    return graph.compile()


def run_pipeline(
    state: TriageState,
    claude: ClaudeClient,
    rag: RAGIndex,
    *,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> TriageState:
    """Run the full triage pipeline and return the updated TriageState.

    Raises BudgetExceeded before the graph starts if the estimated token
    consumption exceeds max_tokens.  This is a conservative estimate; actual
    usage is tracked in real time by ClaudeClient via budget.consume().
    """
    _check_budget(state, max_tokens)
    _budget.set_budget(max_tokens)

    compiled = build_graph(claude, rag)
    result = compiled.invoke(state)

    # LangGraph returns a dict when the state is a Pydantic model.
    if isinstance(result, dict):
        return TriageState(**result)
    return result


def _check_budget(state: TriageState, max_tokens: int) -> None:
    """Estimate token usage and refuse to start if the cap would be blown.

    Estimate formula:
      (raw_diff + contributing_md + agents_md + merged_titles) / 4
      + 8 retrieved chunks × ~1000 chars / 4         (RAG overhead)
      + 4000 flat overhead                            (system prompts, response budgets)
    """
    text_chars = (
        len(state.raw_diff or "")
        + len(state.contributing_md or "")
        + len(state.agents_md or "")
        + sum(len(t) for t in state.recent_merged_titles)
    )
    rag_overhead_tokens = (8 * 1000) // 4  # 8 chunks × 1000 chars average
    flat_overhead = 4_000
    estimated = text_chars // 4 + rag_overhead_tokens + flat_overhead

    if estimated > max_tokens:
        raise BudgetExceeded(
            f"Pre-flight estimate of ~{estimated:,} tokens exceeds the "
            f"{max_tokens:,}-token cap. Use --max-tokens to raise the limit."
        )
